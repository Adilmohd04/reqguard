from orders.service import Order, ship_order

def test_open_order_ships():
    order = Order(id="A1", status="open", quantity=5)
    assert ship_order(order).status == "shipped"

def test_ship_returns_the_order():
    order = Order(id="A2", status="open", quantity=1)
    assert ship_order(order).id == "A2"
# NOTE: nothing constructs a cancelled order. That is the gap.
