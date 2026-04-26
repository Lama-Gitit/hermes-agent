"""
Courtyard → Alchemy enumerator + Courtyard metadata hydrator.

Two-step fetch:
  1. Alchemy `getNFTsForContract` gives us a paginated stream of tokenIds
     and tokenUris for the Courtyard ERC-721 contract on Polygon.
  2. Each tokenUri points at api.courtyard.io's *public* metadata.json
     (no auth needed). We hit it to hydrate the real slab data:
     the `proof_of_integrity.fingerprint` string contains the exact
     graded slab description, e.g.
       "Baseball | PSA 97322199 | 2021 Bowman Draft BDC200 Tyler Black | 9 MINT"
     which we parse into structured attributes.

Usage notes:
  - `demo` Alchemy key works for small / infrequent calls but is rate-limited.
    Store a free key in env var ALCHEMY_POLYGON_API_KEY for real use.
  - Source `notes` column can carry a JSON blob with config, e.g.
      {"page_size": 100, "max_pages": 3, "start_page_key": null, "hydrate": true}
    - page_size    : Alchemy page size (max 100)
    - max_pages    : how many pages to fetch per run
    - start_page_key: resume from a previous run's pageKey
    - hydrate      : whether to fetch metadata.json per token (default true)
  - We emit one `fundamental` entry per token snapshot.
    dedup_key = contract + tokenId (token is unique forever).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from tools.fetchers.base import FetcherAdapter, FetchEntry, FetchResult, register

logger = logging.getLogger(__name__)

_CONTRACT = "0x251be3a17af4892035c37ebf5890f4a4d889dcad"


def _alchemy_base() -> str:
    key = os.environ.get("ALCHEMY_POLYGON_API_KEY") or "demo"
    return f"https://polygon-mainnet.g.alchemy.com/nft/v3/{key}"


def _parse_config(notes: Optional[str]) -> Dict[str, Any]:
    """Parse the optional JSON config stashed in hermes_sources.notes."""
    if not notes:
        return {}
    try:
        cfg = json.loads(notes)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _parse_fingerprint(fp: Optional[str]) -> Dict[str, Any]:
    """
    Courtyard fingerprints look like:
      "Baseball | PSA 97322199 | 2021 Bowman Draft BDC200 Tyler Black | 9 MINT"
      "Pokemon | PSA 12345678 | 2019 Pokemon Hidden Fates SV49 Charizard Shiny | 10 GEM MT"

    Layout is pipe-delimited, trimmed. Field 1 = category, field 2 = grader +
    serial, field 3 = free-form card line, field 4 = grade. We split on "|"
    first, then pull tokens.
    """
    out: Dict[str, Any] = {}
    if not fp:
        return out
    parts = [p.strip() for p in fp.split("|") if p.strip()]
    if len(parts) >= 1:
        out["category"] = parts[0]
    if len(parts) >= 2:
        grader_tokens = parts[1].split()
        if grader_tokens:
            out["grader"] = grader_tokens[0]              # PSA / CGC / BGS
            if len(grader_tokens) > 1:
                out["serial"] = " ".join(grader_tokens[1:])
    if len(parts) >= 3:
        out["card_line"] = parts[2]
        # Heuristic: first token in the card_line that's 4 digits = year
        for tok in parts[2].split():
            if tok.isdigit() and len(tok) == 4:
                out["year"] = tok
                break
    if len(parts) >= 4:
        out["grade"] = parts[3]
    out["fingerprint"] = fp
    return out


def _fetch_metadata_json(token_uri: str, adapter: "CourtyardAlchemyCatalog") -> Optional[Dict[str, Any]]:
    """Pull and parse the public metadata.json from api.courtyard.io."""
    if not token_uri:
        return None
    try:
        return adapter.http_json(token_uri, timeout=15, retries=1)
    except Exception as e:
        logger.info("[courtyard_alchemy] metadata fetch failed for %s: %s", token_uri, e)
        return None


def _extract_card_attrs(nft: Dict[str, Any], hydrated: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge OpenSea-style attributes (if any) with our fingerprint parse."""
    flat: Dict[str, Any] = {}
    md = (nft.get("raw") or {}).get("metadata") or {}
    for a in md.get("attributes") or []:
        if not isinstance(a, dict):
            continue
        k = a.get("trait_type") or a.get("key")
        v = a.get("value")
        if k:
            flat[str(k).lower().replace(" ", "_")] = v

    if hydrated:
        ti = (hydrated.get("token_info") or {}).get("proof_of_integrity") or {}
        parsed = _parse_fingerprint(ti.get("fingerprint"))
        flat.update(parsed)

    return flat


@register
class CourtyardAlchemyCatalog(FetcherAdapter):
    source_type = "courtyard_alchemy"
    default_credibility = "tier1"   # fundamentals (on-chain truth about the slab)
    required_env = []               # optional: ALCHEMY_POLYGON_API_KEY

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        source_id = source_row["id"]
        cfg = _parse_config(source_row.get("notes"))
        page_size = int(cfg.get("page_size", 100))
        max_pages = int(cfg.get("max_pages", 2))
        page_key: Optional[str] = cfg.get("start_page_key")

        hydrate = bool(cfg.get("hydrate", True))

        result = FetchResult(source_id=source_id)
        today = date.today().isoformat()

        base = _alchemy_base()
        seen_tokens: List[Dict[str, Any]] = []

        for page_idx in range(max_pages):
            url = (
                f"{base}/getNFTsForContract"
                f"?contractAddress={_CONTRACT}"
                f"&withMetadata=true"
                f"&limit={page_size}"
            )
            if page_key:
                url += f"&pageKey={page_key}"

            try:
                data = self.http_json(url, timeout=25, retries=2)
            except Exception as e:
                if page_idx == 0:
                    return result.mark_done("error", f"Alchemy fetch failed: {e}")
                logger.warning("[courtyard_alchemy] page %d failed: %s — stopping", page_idx, e)
                break

            nfts = data.get("nfts") or []
            seen_tokens.extend(nfts)
            page_key = data.get("pageKey")
            if not page_key or not nfts:
                break

        result.items_found = len(seen_tokens)

        common_source = {
            "url": f"https://opensea.io/collection/courtyard-nft",
            "source_type": self.source_type,
            "author": "alchemy_nft_v3",
            "author_credibility": self.default_credibility,
            "chain": "polygon",
            "contract": _CONTRACT,
        }

        for nft in seen_tokens:
            token_id = nft.get("tokenId") or ((nft.get("id") or {}).get("tokenId"))
            token_uri = nft.get("tokenUri") or (nft.get("raw") or {}).get("tokenUri")
            if not token_id:
                continue

            hydrated: Optional[Dict[str, Any]] = None
            if hydrate and token_uri:
                hydrated = _fetch_metadata_json(token_uri, self)

            attrs = _extract_card_attrs(nft, hydrated)
            name = (
                (hydrated or {}).get("name")
                or nft.get("name")
                or (nft.get("raw") or {}).get("metadata", {}).get("name")
            )
            image = (
                (hydrated or {}).get("image")
                or (nft.get("image") or {}).get("cachedUrl")
                or (nft.get("image") or {}).get("originalUrl")
            )
            external_url = (hydrated or {}).get("external_url")

            # Best-effort card_id build — prefer clean (set, card_number) when
            # present, fall back to (year + card_line) from fingerprint parse.
            card_id = "unresolved"
            grader = str(attrs.get("grader") or "").lower()
            grade = str(attrs.get("grade") or "").lower().replace(" ", "")
            year = str(attrs.get("year") or "").lower()
            card_set = str(attrs.get("set") or attrs.get("set_name") or "").lower().replace(" ", "-")
            card_no = str(attrs.get("card_number") or attrs.get("number") or "").lower().replace(" ", "")
            card_line = str(attrs.get("card_line") or "").lower().replace(" ", "-")

            if card_set and card_no and grader and grade:
                card_id = f"{card_set}-{card_no}-{grader}{grade}".strip("-")
            elif year and card_line and grader and grade:
                card_id = f"{year}-{card_line[:40]}-{grader}{grade}".strip("-")

            result.entries.append(
                FetchEntry(
                    card_id=card_id,
                    claim_type="fundamental",
                    confidence="canonical",  # on-chain tokenURI is canonical
                    date_observed=today,
                    value={
                        "metric": "vaulted_token",
                        "token_id": str(token_id),
                        "token_name": name,
                        "attributes": attrs,
                        "image_url": image,
                        "external_url": external_url,
                        "token_uri": token_uri,
                        "observed_at": self.now_iso(),
                    },
                    source=common_source,
                    # dedup_key = contract+tokenId — token is unique per collection
                    dedup_key=self.stable_hash("courtyard", _CONTRACT, token_id),
                )
            )

        if not result.entries:
            return result.mark_done("partial", "no tokens returned")
        return result.mark_done("success")
