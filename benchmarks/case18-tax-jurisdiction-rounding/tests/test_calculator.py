from tax.calculator import compute_sales_tax

def test_standard_tax_calculation():
    assert compute_sales_tax("100.00", "8.0") == "8.00"

def test_zero_tax_rate():
    assert compute_sales_tax("50.00", "0.0") == "0.00"
# NOTE: No test asserts half-to-even tie breaking at .005 cent fractions.
