from inventory.stock import StockItem, reserve_stock

def test_reserve_available_stock():
    item = StockItem(sku="SKU-01", available=10)
    reserve_stock(item, 4)
    assert item.available == 6

def test_reserve_all_stock():
    item = StockItem(sku="SKU-02", available=5)
    reserve_stock(item, 5)
    assert item.available == 0
# NOTE: No test attempts overdrawing inventory.
