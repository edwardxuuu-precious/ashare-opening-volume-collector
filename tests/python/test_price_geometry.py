import json
import unittest
from decimal import Decimal
from pathlib import Path
from backend.chips.price_geometry import geometric_level, reflected_price

VECTORS = json.loads((Path(__file__).parent.parent / 'fixtures' / 'log-price-vectors.json').read_text())

class PriceGeometryTests(unittest.TestCase):
    def test_shared_vectors(self):
        for item in VECTORS['levels']:
            actual = geometric_level(Decimal(item['low']), Decimal(item['high']), Decimal(item['ratio']))
            self.assertAlmostEqual(float(actual), item['expected'], places=12)
        for item in VECTORS['reflections']:
            actual = reflected_price(Decimal(item['origin']), Decimal(item['middle']))
            self.assertAlmostEqual(float(actual), item['expected'], places=12)
        self.assertEqual(str(reflected_price(Decimal(3), Decimal(2))), '1.333333333333333333333333333')

    def test_invalid_price_rejected(self):
        for value in (Decimal(0), Decimal(-1), Decimal('NaN')):
            with self.assertRaises(ValueError):
                reflected_price(value, Decimal(1))
