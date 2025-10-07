from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


# ---------- USER ----------
class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    # /register in app.py doesn't set name -> keep nullable
    name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # consumer, annachi, farmer, ngo
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ✅ Annachi location / service fields
    shop_lat = db.Column(db.Float)                        # Annachi shop latitude
    shop_lng = db.Column(db.Float)                        # Annachi shop longitude
    pincode = db.Column(db.String(10))                    # Annachi's base pincode
    service_radius_km = db.Column(db.Integer, default=5)  # match app.py default/migration

    # Relationships
    products = db.relationship("Product", backref="owner", lazy=True)
    addresses = db.relationship("Address", backref="user", lazy=True)

    # helpers for password hashing
    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)


# ---------- PRODUCT ----------
class Product(db.Model):
    __tablename__ = "product"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(50), nullable=False)   # Cereals, Fruits, Vegetables

    # Pricing/qty fields
    price = db.Column(db.Float, nullable=False)
    # legacy quantity (not used by admin upserts) -> safe defaults
    quantity = db.Column(db.Integer, nullable=True, default=0)

    # legacy pincode (you now use pincode) -> keep but make nullable
    pin_code = db.Column(db.String(6), nullable=True)

    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    # ✅ fields used by current app.py & UI
    mrp = db.Column(db.Float)                              # optional MRP
    stock = db.Column(db.Integer, default=0)               # inventory field
    pincode = db.Column(db.String(10))                     # unified pincode
    image_url = db.Column(db.String(300))                  # product image
    total_purchased = db.Column(db.Integer, default=0)     # running sales counter
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------- ADDRESS ----------
class Address(db.Model):
    __tablename__ = "address"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    label = db.Column(db.String(50))       # Home, Work, etc.
    line = db.Column(db.String(200))       # street/house
    city = db.Column(db.String(100))
    pin_code = db.Column(db.String(6))
    phone = db.Column(db.String(10))


# ---------- ORDER BUNDLE (optional; kept, no hard link to Order) ----------
class OrderBundle(db.Model):
    __tablename__ = "order_bundle"

    id = db.Column(db.Integer, primary_key=True)
    consumer_id = db.Column(db.Integer, db.ForeignKey("user.id"))         # who placed it
    annachi_id = db.Column(db.Integer, db.ForeignKey("user.id"))          # single assigned shop
    status = db.Column(db.String(20), default="Pending")                   # Pending, Packed, Delivered
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    consumer = db.relationship("User", foreign_keys=[consumer_id], lazy=True)
    annachi = db.relationship("User", foreign_keys=[annachi_id], lazy=True)

    # NOTE: We don't declare a relationship to Order because Order.bundle_id is a String(64)
    # per app.py migrations. If you later migrate to an integer FK, you can add:
    # orders = db.relationship("Order", backref="bundle", lazy=True)


# ---------- ORDER ----------
class Order(db.Model):
    __tablename__ = "order"

    id = db.Column(db.Integer, primary_key=True)
    consumer_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(50), default="Pending")   # Pending, Shipped, Delivered
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ✅ auto-assign target Annachi (decouples from product.owner if needed)
    assigned_annachi_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    # ✅ MUST match app.py: VARCHAR(64), not an FK
    bundle_id = db.Column(db.String(64))

    # Optional relationships
    consumer = db.relationship("User", foreign_keys=[consumer_id], lazy=True)
    assigned_annachi = db.relationship("User", foreign_keys=[assigned_annachi_id], lazy=True)
    product = db.relationship("Product", foreign_keys=[product_id], lazy=True)
