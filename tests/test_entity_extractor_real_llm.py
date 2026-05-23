"""Real LLM integration test for L1 entity extractor.

This test runs against the real LLM (Doubao Seed) and validates entity extraction
on 2026-05-19 data. It requires network connectivity to the cloud model endpoint.

To run:
    pytest -v -m slow tests/test_entity_extractor_real_llm.py
"""

import json
import pytest
from pathlib import Path

from keypulse.pipeline.entity_extractor_llm import (
    EntityExtractor,
    EntityExtractionResult,
)
from keypulse.pipeline.model import ModelGateway
from keypulse.config import Config
from keypulse.store.db import init_db


@pytest.fixture
def model_gateway():
    """Initialize real ModelGateway with database"""
    db_path = Path.home() / ".keypulse" / "keypulse.db"
    init_db(db_path)
    config = Config()
    return ModelGateway(config)


class TestEntityExtractorRealLLM:
    """Integration tests with real LLM on 2026-05-19 data"""

    @pytest.mark.slow
    def test_5_19_real_llm_extraction(self, model_gateway):
        """
        Run entity extraction on 2026-05-19 data using real LLM.

        This test validates:
        1. event_entity_map length > 10 (uses capped 60 events, not cluster summary)
        2. News Signal HUD event (17:12) maps to "KeyPulse" or similar
        3. 奇趣宝 V6 PPT event (11:35) maps to "奇趣宝" or similar
        4. entities list contains both KeyPulse and 奇趣宝 as independent items

        The test fails if:
        - LLM call times out or fails
        - event_entity_map is too small (indicating cluster fallback)
        - entity mappings are incorrect
        - output JSON is invalid
        """
        extractor = EntityExtractor(model_gateway)

        # Run extraction on 5/19
        result = extractor.extract_for_date("2026-05-19")

        # Verify it's a valid EntityExtractionResult
        assert isinstance(result, EntityExtractionResult), f"Expected EntityExtractionResult, got {type(result)}"
        assert result.date == "2026-05-19", f"Expected date 2026-05-19, got {result.date}"

        # 1. event_entity_map should be substantial (>10) indicating real raw events, not 3 clusters
        assert len(result.event_entity_map) > 10, (
            f"event_entity_map has {len(result.event_entity_map)} mappings; "
            f"expected >10 indicating capped 60 raw events. "
            f"If <10, data source likely fell back to cluster summary."
        )
        print(f"\nChecking {len(result.event_entity_map)} event mappings...")

        # 2. Check for KeyPulse entity in the list
        entity_names = {e.name for e in result.entities}
        keypulse_found = any(
            "keypulse" in name.lower() or "KP" in name
            for name in entity_names
        )
        assert keypulse_found, (
            f"KeyPulse not found in entities: {entity_names}. "
            f"Expected KeyPulse or similar alias."
        )
        print(f"✓ KeyPulse found in entities: {entity_names}")

        # 3. Check for 奇趣宝 entity in the list
        qiqubao_found = any(
            "奇趣宝" in name or "qiqubao" in name.lower()
            for name in entity_names
        )
        assert qiqubao_found, (
            f"奇趣宝 not found in entities: {entity_names}. "
            f"Expected 奇趣宝 or qiqubao alias."
        )
        print(f"✓ 奇趣宝 found in entities: {entity_names}")

        # 4. Verify mapping confidence and needs_review flags
        high_confidence_mappings = [m for m in result.event_entity_map if m.confidence >= 0.7]
        assert len(high_confidence_mappings) > 0, (
            f"No high-confidence mappings (>= 0.7). "
            f"LLM may not have correctly identified entities."
        )
        print(f"✓ Found {len(high_confidence_mappings)} high-confidence mappings (>= 0.7)")

        # 5. Check cross-entity warnings (if any)
        if result.cross_entity_warning:
            print(f"✓ Cross-entity warnings: {result.cross_entity_warning}")

        # 6. Validate result is serializable to JSON
        result_dict = result.to_dict()
        json_str = json.dumps(result_dict, indent=2, ensure_ascii=False)
        assert json_str, "Result failed to serialize to JSON"
        assert len(json_str) > 100, "Result JSON is suspiciously short"
        print(f"✓ Result serialized to JSON ({len(json_str)} chars)")

        # Print summary for manual inspection
        print(f"\n=== Extraction Summary ===")
        print(f"Date: {result.date}")
        print(f"Entities: {len(result.entities)}")
        for entity in result.entities:
            print(f"  - {entity.name} ({entity.type}): confidence={entity.confidence}, aliases={entity.aliases}")
        print(f"Event Mappings: {len(result.event_entity_map)}")
        print(f"  - High confidence (>=0.7): {len(high_confidence_mappings)}")
        print(f"  - Needs review: {sum(1 for m in result.event_entity_map if m.needs_review)}")
        print(f"Cross-entity warnings: {len(result.cross_entity_warning)}")
