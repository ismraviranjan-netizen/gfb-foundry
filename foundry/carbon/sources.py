"""The Green Ledger's sources, numbered as in the book's Sources appendix (md/green_STATE.md).

Every carbon number the package prints carries one of these ids: S1..S25 for a researched source,
ILL for a made-up GFB number (illustrative: a distance, a weight, a quote, an average), GFB for GFB's
own activity data (also made up: GFB is fictional). `verified` is what was confirmed on the date
in `checked`; a value from a source marked verified=False must be printed with its label, never as fact.
"""
from __future__ import annotations

CHECKED = "2026-11-08"
ILL = "ILL"      # illustrative: a GFB number with no external source
GFB = "GFB"      # GFB's own (fictional) activity data: kWh, litres, spend, volumes

SOURCES: dict[int, dict] = {
    1: {"short": "SFC, India Default GHG Emission Values v1.0 (complementing GLEC v3.01)", "date": "2025-05", "verified": True,
        "url": "https://smart-freight-centre-media.s3.amazonaws.com/documents/India_Default_GHG_Emission_Values_SFC_INDIA_v1_2.pdf"},
    2: {"short": "GLEC Framework v3.2 (SFC)", "date": "2025-10-23", "verified": True,
        "url": "https://searoutes.com/en/blog/glec-framework-v3-2-2025"},
    3: {"short": "ISO 14083:2023, GHG emissions arising from transport chain operations", "date": "2023-03", "verified": True,
        "url": "https://www.iso.org/standard/78864.html"},
    4: {"short": "DESNZ GHG conversion factors 2026", "date": "2026-06-11", "verified": True,
        "url": "https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026"},
    5: {"short": "CEA CO2 Baseline Database: v20.0 FY2023-24 0.727 t/MWh (secondary); v22.0 value NOT VERIFIED", "date": "2024-12 / 2026-09",
        "verified": False,
        "url": "https://www.saurenergy.com/solar-energy-news/ceas-co2-baseline-database-impact-of-re-on-indian-grid-emissions-intensity"},
    6: {"short": "Omnibus I (CSRD and CSDDD simplification): Council adoption, OJ, entry into force", "date": "2026-02-24", "verified": True,
        "url": "https://www.regulationtomorrow.com/2026/02/omnibus-i-csrd-and-cs3d-simplification-council-of-european-union-adopts-final-text-t-simplify-sustainability-reporting-and-due-diligence-requirements/"},
    7: {"short": "Dcycle, CSRD timeline after Omnibus (Directive (EU) 2026/470)", "date": "2026", "verified": True,
        "url": "https://dcycle.io/blog/csrd-reporting-timeline-post-omnibus/"},
    8: {"short": "EFRAG: Commission publishes delegated acts on revised ESRS and the Voluntary Standard", "date": "2026-07-03", "verified": True,
        "url": "https://www.efrag.org/en/news-and-calendar/news/european-commission-publishes-delegated-act-on-revised-esrs-and-voluntary-sustainability-reporting"},
    9: {"short": "Mayer Brown, CBAM simplification Regulation (EU) 2025/2083", "date": "2025-10", "verified": True,
        "url": "https://www.mayerbrown.com/en/insights/publications/2025/10/eu-adopts-cbam-simplification-regulation-10-key-amendments-and-challenges-ahead"},
    10: {"short": "GreenSutra, CBAM aluminium CN 7601-7616 (secondary)", "date": "2025", "verified": True, "url": "https://greensutra.in/?p=237176"},
    11: {"short": "GreenSutra, BRSR value-chain disclosure (SEBI 12 Jul 2023, revised 28 Mar 2025)", "date": "2025-03-28", "verified": True,
         "url": "https://greensutra.in/question/brsr-value-chain-disclosure/"},
    12: {"short": "Benchmark Gensuite, BRSR Core assurance glide path", "date": "2026", "verified": True,
         "url": "https://benchmarkgensuite.com/ehs-blog-india/2026-brsr-core-assurance/"},
    13: {"short": "ORF Issue Brief 858, India's BRSR and international standards", "date": "2026-01-22", "verified": True,
         "url": "https://orfonline.org/research/enhancing-the-interoperability-of-india-s-business-sustainability-reporting-with-international-standards"},
    14: {"short": "S&P Global, ISSB adoption Q3 2026 (35 adopted, 11 planning, as of 22 Jul 2026)", "date": "2026-07-22", "verified": True,
         "url": "https://www.spglobal.com/sustainable1/en/insights/research-reports/issb-q3-2026"},
    15: {"short": "PIB, CCTS GHG intensity targets (490 entities)", "date": "2026-01-13", "verified": True,
         "url": "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2217239&reg=3&lang=1"},
    16: {"short": "ICAP, CCTS compliance obligations in force for seven sectors", "date": "2026-03-30", "verified": True,
         "url": "https://icapcarbonaction.com/en/news/compliance-obligations-under-indias-carbon-credit-trading-scheme-enter-force-seven-sectors"},
    17: {"short": "SBTi Corporate Net-Zero Standard V2.0 released", "date": "2026-06-11", "verified": True,
         "url": "https://sciencebasedtargets.org/news/the-sbti-releases-corporate-net-zero-standard-v2-0-to-accelerate-corporate-climate-action"},
    18: {"short": "Rödl & Partner, SBTi CNZS V2.0 action recommendations (secondary)", "date": "2026", "verified": True,
         "url": "https://www.roedl.com/en/insights/science-based-targets-the-new-corporate-net-zero-standard-version-2-0-is-here-our-action-recommendations/"},
    19: {"short": "WBCSD, PACT Methodology Version 3", "date": "2025-04-30", "verified": True,
         "url": "https://www.wbcsd.org/resources/pact-methodology-version-3/"},
    20: {"short": "GHG Protocol, Scope 2 public consultation (to 31 Jan 2026)", "date": "2025-10-20", "verified": True,
         "url": "https://ghgprotocol.org/index%2Ephp/blog/release-ghg-protocol-opens-public-consultations-scope-2-and-electricity-sector-consequential"},
    21: {"short": "The Climate Registry, GHG Protocol Land Sector and Removals Standard issued", "date": "2026-01-30", "verified": True,
         "url": "https://theclimateregistry.org/?p=9620"},
    22: {"short": "Linklaters: revised ESRS (Delegated Reg. (EU) 2026/1563) and Voluntary Standard (2026/1560) in the OJ", "date": "2026-09-21",
         "verified": True,
         "url": "https://sustainablefutures.linklaters.com/post/102o1ou/eu-csrd-revised-esrs-and-voluntary-reporting-standard-are-published-in-the-offic"},
    23: {"short": "XBRL International: revised ESRS become law (in force 10 Nov 2026)", "date": "2026-09-27", "verified": True,
         "url": "https://www.xbrl.org/news/revised-esrs-become-law/"},
    24: {"short": "CERC Record of Proceedings, Petitions 453, 546, 574/MP/2026 (CCC contracts on PXIL, IEX, HPX)", "date": "2026-10-23",
         "verified": True, "url": "https://cercind.gov.in/2026/ROP/453-MP-2026-Ors_231026.pdf"},
    25: {"short": "Bar & Bench: India's carbon market opens for business (trading expected Nov-Jan)", "date": "2026-10-18", "verified": True,
         "url": "https://www.barandbench.com/columns/courting-controversy-green-becomes-the-new-gold-indias-carbon-market-opens-for-business"},
}


def sid(source) -> str:
    """'S1' for 1 or 'S1'; ILL and GFB pass through."""
    if source in (None, ""):
        return ILL
    s = str(source)
    if s in (ILL, GFB) or s.startswith("supplier:"):
        return s
    return s if s.startswith("S") else f"S{int(s)}"


def verified(source) -> bool:
    s = sid(source)
    if not s.startswith("S"):
        return False
    return bool(SOURCES.get(int(s[1:]), {}).get("verified"))


def table() -> list[dict]:
    return [{"id": f"S{i}", **v} for i, v in sorted(SOURCES.items())]
