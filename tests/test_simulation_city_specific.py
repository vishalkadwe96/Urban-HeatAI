import unittest
from unittest.mock import patch

from backend.config import CITY_REGISTRY, SIM_SCENARIOS
from backend.services.recommendation_service import _estimate_health_impact, _simulate_with_model


class CitySpecificSimulationTests(unittest.TestCase):
    def test_health_impact_scales_to_configured_city_population(self):
        zones = [
            {"population_density": 100},
            {"population_density": 900},
        ]

        delhi = _estimate_health_impact(CITY_REGISTRY["delhi"], zones, 100, 50)
        mumbai = _estimate_health_impact(CITY_REGISTRY["mumbai"], zones, 100, 50)

        self.assertEqual(delhi["people_benefited"], 1_550_000)
        self.assertEqual(mumbai["people_benefited"], 1_035_000)
        self.assertEqual(delhi["projected_deaths_prevented"], 38.8)
        self.assertEqual(mumbai["projected_deaths_prevented"], 25.9)

    @patch("backend.services.recommendation_service._base_lst_for_city", return_value=(30, {}))
    @patch("backend.services.recommendation_service._generate_grid")
    @patch("backend.services.recommendation_service.predict_lst_batch")
    def test_model_temperature_change_is_bounded_by_intervention(self, predict_batch, generate_grid, _base_lst):
        generate_grid.return_value = [
            {"lst": 38.0, "ndvi": 0.3, "population_density": 3000, "urban_index": 0.8,
             "risk_level": "High", "uhi_intensity": 2.0},
            {"lst": 37.0, "ndvi": 0.4, "population_density": 1000, "urban_index": 0.5,
             "risk_level": "High", "uhi_intensity": 1.0},
        ]
        predict_batch.side_effect = [[40.0, 37.0], [45.0, 10.0]]
        scenario = SIM_SCENARIOS["green_cover"]

        result = _simulate_with_model("delhi", CITY_REGISTRY["delhi"], scenario, "green_cover", 20)

        self.assertGreaterEqual(result["projected_state"]["temp_reduction"], 0)
        self.assertLessEqual(
            result["projected_state"]["temp_reduction"],
            scenario["lst_factor"] * 20,
        )
        self.assertLessEqual(
            result["health_impact"]["people_benefited"],
            CITY_REGISTRY["delhi"]["population"],
        )


if __name__ == "__main__":
    unittest.main()