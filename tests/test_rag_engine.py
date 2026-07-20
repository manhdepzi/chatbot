from backend.rag_engine import best_quote_memory_candidate, quote_memory_similarity


def test_rag_candidate_auto_applies_only_when_match_is_strong():
    item = {
        "description": "Cua gio nan bau duc 600x600 kem OBD",
        "category": "SQUARE_DIFFUSER",
        "width": 600,
        "height": 600,
        "unit": "cai",
    }
    rows = [
        {
            "normalized_description": "cua gio nan bau duc 600x600 kem obd",
            "category": "SQUARE_DIFFUSER",
            "width": 600,
            "height": 600,
            "unit": "cai",
            "quote_unit_price": 588540,
        },
        {
            "normalized_description": "cua gio nan bau duc 800x600 kem obd",
            "category": "SQUARE_DIFFUSER",
            "width": 800,
            "height": 600,
            "unit": "cai",
            "quote_unit_price": 700000,
        },
    ]

    candidate = best_quote_memory_candidate(item, rows)

    assert candidate is not None
    assert candidate["quote_unit_price"] == 588540
    assert candidate["rag_auto_apply"] is True


def test_rag_candidate_does_not_auto_apply_weak_match():
    item = {
        "description": "Van MFD 3000x1200 EI60",
        "category": "MOTORIZED_DAMPER",
        "width": 3000,
        "height": 1200,
        "unit": "cai",
    }
    memory = {
        "normalized_description": "van mfd 1000x400 ei60",
        "category": "MOTORIZED_DAMPER",
        "width": 1000,
        "height": 400,
        "unit": "cai",
        "quote_unit_price": 9000000,
    }

    assert quote_memory_similarity(item, memory) < 0.92
    assert best_quote_memory_candidate(item, [memory])["rag_auto_apply"] is False
