from decimal import Decimal

from django.conf import settings

from carton import module_loading
from carton import settings as carton_settings

try:
    from importlib import import_module
except ImportError:
    from django.utils.importlib import import_module

class CartItem(object):
    """
    A cart item, with the associated product, its quantity and its price.
    """
    def __init__(self, product, product_type, quantity, price):
        self.name = product.title if product_type == settings.PRODUCT_TYPE.Resource else product.name
        self.product = product
        self.product_type = product_type
        self.quantity = int(quantity)
        self.price = Decimal(str(price))

    def __repr__(self):
        return u'CartItem Object (%s)' % self.product

    def to_dict(self):
        return {
            'name': self.product.title if self.product_type == settings.PRODUCT_TYPE.Resource else product.name,
            'product_module': "{}.{}".format(self.product.__class__.__module__, self.product.__class__.__name__),
            'product_type': self.product_type,
            'product_source_id': self.product.id,
            'quantity': self.quantity,
            'price': str(self.price),
        }

    @property
    def subtotal(self):
        """
        Subtotal for the cart item.
        """
        return self.price * self.quantity

    @property
    def unique_id(self):
        return "{}-{}".format(self.product_type, self.product.id)


class Cart(object):

    """
    A cart that lives in the session.
    """
    def __init__(self, session, session_key=None):
        self._items_dict = {}
        self.session = session
        self.session_key = session_key or carton_settings.CART_SESSION_KEY
        # If a cart representation was previously stored in session, then we
        if self.session_key in self.session:
            # rebuild the cart object from that serialized representation.
            cart_representation = self.session[self.session_key]

            for k, v in cart_representation.iteritems():
                module, package = v["product_module"].rsplit('.', 1)
                instance = getattr(import_module(module), package)

                item = instance.objects.get(pk=v["product_source_id"])
                if item:
                    self._items_dict[k] = CartItem(item, v["product_type"], v["quantity"], Decimal(v["price"]))
                    
    def __contains__(self, product):
        """
        Checks if the given product is in the cart.
        """
        return product in self.products

    def get_product_model(self):
        return module_loading.get_product_model()

    def filter_products(self, queryset):
        """
        Applies lookup parameters defined in settings.
        """
        lookup_parameters = getattr(settings, 'CART_PRODUCT_LOOKUP', None)
        if lookup_parameters:
            queryset = queryset.filter(**lookup_parameters)
        return queryset

    def get_queryset(self):
        product_model = self.get_product_model()
        queryset = product_model._default_manager.all()
        queryset = self.filter_products(queryset)
        return queryset

    def update_session(self):
        """
        Serializes the cart data, saves it to session and marks session as modified.
        """
        self.session[self.session_key] = self.cart_serializable
        self.session.modified = True

    def add(self, product, price=None, quantity=1):
        """
        Adds or creates products in cart. For an existing product,
        the quantity is increased and the price is ignored.
        """
        quantity = int(quantity)
        if quantity < 1:
            raise ValueError('Quantity must be at least 1 when adding to cart')
        if product in self.products:
            self._items_dict["{}-{}".format(product.product_type, product.product_source_id)].quantity += quantity
        else:
            if price == None:
                raise ValueError('Missing price when adding to cart')

            module, package = product.get_module_for_product_type().rsplit('.', 1)
            instance = getattr(import_module(module), package)

            item = instance.objects.get(pk=product.product_source_id)
            if item:
                self._items_dict["{}-{}".format(product.product_type, product.product_source_id)] = CartItem(item, product.product_type, quantity, price)
        self.update_session()

    def remove(self, product, product_type):
        """
        Removes the product.
        """
        if product in self.products:
            del self._items_dict["{}-{}".format(product_type, product.id)]
            self.update_session()

    def remove_single(self, product, product_type):
        """
        Removes a single product by decreasing the quantity.
        """
        if product in self.products:
            if self._items_dict["{}-{}".format(product_type, product.id)].quantity <= 1:
                # There's only 1 product left so we drop it
                del self._items_dict["{}-{}".format(product_type, product.id)]
            else:
                self._items_dict["{}-{}".format(product_type, product.id)].quantity -= 1
            self.update_session()

    def clear(self):
        """
        Removes all items.
        """
        self._items_dict = {}
        self.update_session()

    def set_quantity(self, product, quantity):
        """
        Sets the product's quantity.
        """
        quantity = int(quantity)
        if quantity < 0:
            raise ValueError('Quantity must be positive when updating cart')
        if product in self.products:
            self._items_dict["{}-{}".format(product.product_type, product.product_source_id)].quantity = quantity
            if self._items_dict["{}-{}".format(product.product_type, product.product_source_id)].quantity < 1:
                del self._items_dict[product.pk]
            self.update_session()

    @property
    def items(self):
        """
        The list of cart items.
        """
        return self._items_dict.values()

    @property
    def cart_serializable(self):
        """
        The serializable representation of the cart.
        For instance:
        {
            '1': {'product_pk': 1, 'quantity': 2, price: '9.99'},
            '2': {'product_pk': 2, 'quantity': 3, price: '29.99'},
        }
        Note how the product pk servers as the dictionary key.
        """
        cart_representation = {}
        for item in self.items:
            # JSON serialization: object attribute should be a string
            product_source_id = item.product.id
            product_type = item.product_type
            cart_representation["{}-{}".format(product_type, product_source_id)] = item.to_dict()
        return cart_representation


    @property
    def items_serializable(self):
        """
        The list of items formatted for serialization.
        """
        return self.cart_serializable.items()

    @property
    def count(self):
        """
        The number of items in cart, that's the sum of quantities.
        """
        return sum([item.quantity for item in self.items])

    @property
    def unique_count(self):
        """
        The number of unique items in cart, regardless of the quantity.
        """
        return len(self._items_dict)

    @property
    def is_empty(self):
        return self.unique_count == 0

    @property
    def products(self):
        """
        The list of associated products.
        """
        return [item.product for item in self.items]

    @property
    def total(self):
        """
        The total value of all items in the cart.
        """
        return sum([item.subtotal for item in self.items])
