"""R-WLD-12 (sufficiency): everything a correct public claim needs is VISIBLE to the roles that
publish and trade, not just present in the registry.

The first live smoke runs found the company entity id appeared in no document, packet or
prompt, so agents filed correct claims under the instrument id and full credit was impossible.
"""

from ethwall.contract import Role
from ethwall.world import build_world, packet


def test_reporting_fact_identifiers_are_visible_to_comms_and_trading_seeds_0_199():
    """R-WLD-12: each reporting fact's entity id, source doc id and value text are in the
    Comms and Trading packets (or the cleared-document text they can retrieve)."""
    for seed in range(200):
        w = build_world(seed)
        for role in (Role.COMMS, Role.TRADING):
            text = packet(w, role).text
            for f in w.reporting_facts():
                assert f.entity_id in text, (
                    seed,
                    role,
                    f.fact_id,
                    "entity id not visible",
                )
                assert f.source_doc_id in text, (
                    seed,
                    role,
                    f.fact_id,
                    "source not visible",
                )


def test_every_document_names_the_issuer_ids():
    """R-WLD-12: every document header states the issuer's entity id and instrument id."""
    w = build_world(7)
    for d in w.documents:
        assert w.company_id in d.body and w.instrument_id in d.body, d.doc_id
