from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json
from queue import SimpleQueue
import math
import sqlite3
from sqlalchemy import or_
import os  # ✅ added for env + Render detection

# ---------- APP SETUP ----------
app = Flask(__name__)
# ✅ use env in production; fallback for local
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "super-secret")

# ✅ persistent DB path on Render (with Disk at /var/data), local fallback
DB_PATH = "/var/data/sasyanova.db" if os.getenv("RENDER") else "sasyanova.db"
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# 🔒 FIXED CATALOG CATEGORIES (no free-form)
FIXED_CATS = {"cereals", "fruits", "vegetables"}

# 🔐 ADMIN TOKEN (SasyaNova) — CHANGE THIS IN PRODUCTION
ADMIN_TOKEN = "change-me-admin-token"


# ---------- MODELS ----------
class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # consumer / annachi / farmer / ngo

    # Annachi location/service fields (nullable so legacy users work)
    shop_lat = db.Column(db.Float)
    shop_lng = db.Column(db.Float)
    pincode = db.Column(db.String(10))
    service_radius_km = db.Column(db.Integer, default=5)

    products = db.relationship("Product", backref="owner", lazy=True)


class Product(db.Model):
    __tablename__ = "product"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(50), nullable=False)   # cereals/fruits/vegetables
    mrp = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, nullable=False)
    pincode = db.Column(db.String(10), nullable=False)
    image_url = db.Column(db.String(300))
    total_purchased = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Order(db.Model):   # Order model supports bundles + assignment
    __tablename__ = "order"
    id = db.Column(db.Integer, primary_key=True)
    consumer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default="Pending")   # Pending → Packed → Delivered
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # decouple assignment from product owner (optional)
    assigned_annachi_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    # bundle grouping id (for packing one slip)
    bundle_id = db.Column(db.String(64))  # nullable for legacy rows

    consumer = db.relationship("User", backref="orders", lazy=True, foreign_keys=[consumer_id])
    product = db.relationship("Product", backref="orders", lazy=True)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---------- REALTIME (SSE) ----------
subscribers = set()

def event_stream():
    q = SimpleQueue()
    subscribers.add(q)
    try:
        while True:
            data = q.get()
            yield f"data: {json.dumps(data)}\n\n"
    finally:
        subscribers.discard(q)

def broadcast(data: dict):
    dead = []
    for q in list(subscribers):
        try:
            q.put_nowait(data)
        except Exception:
            dead.append(q)
    for q in dead:
        subscribers.discard(q)

@app.route("/annachi/orders/stream")
@login_required
def annachi_orders_stream():
    if current_user.role != "annachi":
        return "Unauthorized", 403
    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })

@app.route("/consumer/orders/stream")
@login_required
def consumer_orders_stream():
    if current_user.role != "consumer":
        return "Unauthorized", 403
    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# ---------- HOME ----------
@app.route("/")
def home():
    return render_template("index.html")


# ---------- REGISTER ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        role = request.form["role"]
        email = request.form["email"]
        password = request.form["password"]

        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("Email already registered ❌", "danger")
            return redirect(url_for("register"))

        new_user = User(
            email=email,
            role=role,
            password=generate_password_hash(password)
        )
        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful ✅ Please login", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


# ---------- LOGIN ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form["role"]
        email = request.form["email"]
        password = request.form["password"]

        user = User.query.filter_by(email=email, role=role).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            session["user_id"] = user.id
            session["role"] = user.role
            flash("Login successful ✅", "success")

            if role == "consumer":
                return redirect(url_for("consumer_dashboard"))
            elif role == "annachi":
                return redirect(url_for("annachi_dashboard"))
            elif role == "farmer":
                return redirect(url_for("farmer_dashboard"))
            else:
                return redirect(url_for("ngo_dashboard"))
        else:
            flash("Invalid credentials ❌", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")


# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    logout_user()
    session.clear()
    flash("Logged out ✅", "info")
    return redirect(url_for("home"))


# ---------- DASHBOARDS ----------
@app.route("/dashboard/consumer")
@login_required
def consumer_dashboard():
    if current_user.role != "consumer":
        return redirect(url_for("home"))
    return render_template("consumer_dashboard.html")

@app.route("/dashboard/consumer/orders")
@login_required
def consumer_orders_page():
    if current_user.role != "consumer":
        return redirect(url_for("home"))

    orders = (Order.query
              .filter_by(consumer_id=current_user.id)
              .join(Product)
              .all())

    gross = sum([(o.product.price if o.product else 0) * (o.quantity or 0) for o in orders])
    commission = round(gross * 0.1, 2)
    net = round(gross - commission, 2)
    metrics = {
        "total_orders": len(orders),
        "earnings_gross": round(gross, 2),
        "commission": commission,
        "earnings_net": net
    }

    return render_template("consumer_orders.html", orders=orders, metrics=metrics)


@app.route("/dashboard/annachi")
@login_required
def annachi_dashboard():
    if current_user.role != "annachi":
        return redirect(url_for("home"))
    products = Product.query.filter_by(owner_id=current_user.id).all()
    return render_template("annachi_dashboard.html", products=products)


@app.route("/dashboard/annachi/orders")
@login_required
def annachi_orders():
    if current_user.role != "annachi":
        flash("Access denied.", "danger")
        return redirect(url_for("home"))

    # include owned and auto-assigned orders
    orders = (Order.query
              .join(Product)
              .filter(or_(
                  Product.owner_id == current_user.id,
                  Order.assigned_annachi_id == current_user.id
              ))
              .all())

    gross = 0
    delivered_gross = 0
    packed_or_better = 0
    for o in orders:
        line = (o.product.price if o.product else 0) * (o.quantity or 0)
        gross += line
        if o.status in ("Packed", "Delivered"):
            packed_or_better += 1
        if o.status == "Delivered":
            delivered_gross += line
    commission_rate = 0.1
    commission = round(gross * commission_rate, 2)
    net = round(gross - commission, 2)
    metrics = {
        "total_orders": len(orders),
        "earnings_gross": round(gross, 2),
        "commission": commission,
        "earnings_net": net,
        "avg_rating": None,
        "ratings_count": 0,
        "sla_ready_pct": round((packed_or_better / len(orders) * 100), 1) if orders else 0.0
    }

    return render_template("annachi_orders.html", orders=orders, metrics=metrics)


@app.route("/dashboard/farmer")
@login_required
def farmer_dashboard():
    if current_user.role != "farmer":
        return redirect(url_for("home"))
    return render_template("farmer_dashboard.html")


@app.route("/dashboard/ngo")
@login_required
def ngo_dashboard():
    if current_user.role != "ngo":
        return redirect(url_for("home"))
    return render_template("ngo_dashboard.html")


# ---------- ANNACHI APIs ----------
@app.route("/annachi/add", methods=["POST"])
@login_required
def annachi_add():
    if current_user.role != "annachi":
        return redirect(url_for("home"))
    flash("Catalog is fixed by SasyaNova. Annachi cannot add products.", "danger")
    return redirect(url_for("annachi_dashboard"))

@app.route("/annachi/delete/<int:pid>", methods=["POST"])
@login_required
def annachi_delete(pid):
    if current_user.role != "annachi":
        return redirect(url_for("home"))
    flash("Catalog is fixed by SasyaNova. Annachi cannot delete products.", "danger")
    return redirect(url_for("annachi_dashboard"))

@app.route("/annachi/edit/<int:pid>", methods=["POST"])
@login_required
def annachi_edit(pid):
    """
    Annachi can change ONLY stock (NOT image, NOT price/MRP).
    """
    if current_user.role != "annachi":
        return redirect(url_for("home"))
    prod = Product.query.get_or_404(pid)
    if prod.owner_id != current_user.id:
        flash("Unauthorized ❌", "danger")
        return redirect(url_for("annachi_dashboard"))

    stock_val = request.form.get("stock", None)

    try:
        new_stock = int(stock_val) if stock_val is not None else None
    except:
        new_stock = None

    if new_stock is None or new_stock < 0:
        flash("Invalid stock value ❌", "danger")
        return redirect(url_for("annachi_dashboard"))

    prod.stock = new_stock
    # 🔒 do NOT allow annachi to modify image_url anymore
    db.session.commit()
    flash("Stock updated ✅", "success")
    return redirect(url_for("annachi_dashboard"))

# ✅ NEW: GET endpoint used by annachi_dashboard.html to load location
@app.route("/annachi/profile/location", methods=["GET"])
@login_required
def annachi_profile_location_get():
    if current_user.role != "annachi":
        return jsonify({"error": "Unauthorized"}), 403
    return jsonify({
        "ok": True,
        "shop_lat": current_user.shop_lat,
        "shop_lng": current_user.shop_lng,
        "pincode": current_user.pincode,
        "service_radius_km": current_user.service_radius_km,
    })

@app.route("/annachi/profile/location", methods=["POST"])
@login_required
def annachi_profile_location():
    """
    Update Annachi's shop_lat, shop_lng, pincode, and service_radius_km.
    Accepts JSON or form fields.
    """
    if current_user.role != "annachi":
        return jsonify({"error": "Unauthorized"}), 403

    # Accept both JSON and form
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        pincode = (payload.get("pincode") or "").strip()
        lat = payload.get("shop_lat")
        lng = payload.get("shop_lng")
        radius = payload.get("service_radius_km")
    else:
        pincode = (request.form.get("pincode") or "").strip()
        lat = request.form.get("shop_lat")
        lng = request.form.get("shop_lng")
        radius = request.form.get("service_radius_km")

    # Coerce types safely
    try:
        lat = float(lat) if lat not in (None, "",) else None
    except:
        lat = None
    try:
        lng = float(lng) if lng not in (None, "",) else None
    except:
        lng = None
    try:
        radius = int(radius) if radius not in (None, "",) else None
    except:
        radius = None

    # Persist
    if pincode:
        current_user.pincode = pincode
    if lat is not None:
        current_user.shop_lat = lat
    if lng is not None:
        current_user.shop_lng = lng
    if radius is not None and radius >= 0:
        current_user.service_radius_km = radius

    db.session.commit()

    # If this endpoint is called via fetch by the UI, return JSON; otherwise redirect
    if request.is_json:
        return jsonify({"ok": True, "pincode": current_user.pincode, "shop_lat": current_user.shop_lat,
                        "shop_lng": current_user.shop_lng, "service_radius_km": current_user.service_radius_km})
    else:
        flash("Location/profile updated ✅", "success")
        return redirect(url_for("annachi_dashboard"))


# 🔧 UPDATED: accept BOTH numeric order IDs and bundle masters (BUNDLE_...)
@app.route("/annachi/orders/update/<oid>", methods=["POST"])
@login_required
def annachi_orders_update(oid):
    """
    Accepts:
      - /annachi/orders/update/31              -> single order update
      - /annachi/orders/update/BUNDLE_<idstr>  -> update every order in that bundle (owned/assigned to this annachi)
    """
    if current_user.role != "annachi":
        flash("Access denied.", "danger")
        return redirect(url_for("home"))

    new_status = request.form.get("status", None)

    # ---- Bundle master path ----
    if isinstance(oid, str) and oid.startswith("BUNDLE_"):
        bundle_id = oid.replace("BUNDLE_", "", 1)

        # Only rows this Annachi owns OR is assigned to
        rows = (Order.query
                .join(Product)
                .filter(Order.bundle_id == bundle_id)
                .filter(or_(Product.owner_id == current_user.id,
                            Order.assigned_annachi_id == current_user.id))
                .all())

        if not rows:
            flash("Bundle not found or unauthorized ❌", "danger")
            return redirect(url_for("annachi_orders"))

        # If no status provided, keep as is (no-op)
        if new_status:
            for r in rows:
                r.status = new_status
            db.session.commit()

            # Broadcast per-line so existing UIs refresh correctly
            for r in rows:
                try:
                    broadcast({
                        "type": "status_update",
                        "owner_id": r.product.owner_id if r.product else None,
                        "consumer_id": r.consumer_id,
                        "assigned_annachi_id": r.assigned_annachi_id,
                        "order_id": r.id,
                        "status": r.status,
                        "bundle_id": r.bundle_id
                    })
                except Exception as e:
                    app.logger.warning(f"SSE broadcast failed: {e}")

            flash(f"Bundle {bundle_id} → {new_status} ✅", "success")
        else:
            flash(f"Bundle {bundle_id} unchanged (no status sent) ℹ️", "info")

        return redirect(url_for("annachi_orders"))

    # ---- Single order path (numeric id as string) ----
    try:
        oid_int = int(oid)
    except Exception:
        flash("Invalid order id ❌", "danger")
        return redirect(url_for("annachi_orders"))

    order = Order.query.get_or_404(oid_int)
    # allow either product owner or the assigned Annachi to update
    if (not order.product) or (
        order.product.owner_id != current_user.id
        and order.assigned_annachi_id != current_user.id
    ):
        flash("Unauthorized ❌", "danger")
        return redirect(url_for("annachi_orders"))

    if new_status:
        order.status = new_status
        db.session.commit()
        try:
            broadcast({
                "type": "status_update",
                "owner_id": order.product.owner_id if order.product else None,
                "consumer_id": order.consumer_id,
                "assigned_annachi_id": order.assigned_annachi_id,
                "order_id": order.id,
                "status": order.status,
                "bundle_id": order.bundle_id
            })
        except Exception as e:
            app.logger.warning(f"SSE broadcast failed: {e}")
        flash("Order updated ✅", "success")
    else:
        flash("No status sent (order unchanged) ℹ️", "info")

    return redirect(url_for("annachi_orders"))


# ---------- PUBLIC API (Consumer Fetch) ----------
# Legacy endpoint (kept for backward-compat). It lists all products in a pincode (any owner).
@app.route("/api/products/<pincode>/<category>")
def api_products(pincode, category):
    # Category guard (allow 'all' for UI strip, else enforce fixed)
    if category != "all":
        cat = (category or "").lower()
        if cat not in FIXED_CATS:
            return jsonify([])

    q = Product.query.filter_by(pincode=pincode)
    if category != "all":
        q = q.filter_by(category=category.lower())
    products = q.all()
    return jsonify([
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "mrp": p.mrp,
            "price": p.price,
            "stock": p.stock,
            "pincode": p.pincode,
            "image_url": p.image_url,
            "total_purchased": p.total_purchased
        } for p in products
    ])


# ---------- NEW: SELECT A SINGLE NEARBY ANNACHI ----------
def haversine(lat1, lon1, lat2, lon2):
    """Return distance (km) between two lat/lng points."""
    R = 6371.0
    dlat = math.radians((lat2 or 0) - (lat1 or 0))
    dlon = math.radians((lon2 or 0)) - math.radians((lon1 or 0))
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1 or 0)) *
         math.cos(math.radians(lat2 or 0)) *
         math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

def _distance_km(consumer_lat, consumer_lng, a: User):
    if consumer_lat is None or consumer_lng is None:
        return 10**9
    if a.shop_lat is None or a.shop_lng is None:
        return 10**9
    try:
        return haversine(float(consumer_lat), float(consumer_lng), float(a.shop_lat), float(a.shop_lng))
    except Exception:
        return 10**9

def select_best_annachi(consumer_pincode=None, consumer_lat=None, consumer_lng=None):
    """
    Choose exactly one Annachi.
    Priority:
      1) If consumer_pincode provided: prefer annachis with same pincode; among them, nearest by distance (if GPS available) and within service radius.
      2) If none in same pin (or no pin), fallback to nearest overall within service radius (if GPS available).
    If GPS not provided, do NOT enforce service radius; distance becomes 'infinite' but we still pick deterministically by id.
    Returns: (annachi, distance_km or None)
    """
    anns_all = User.query.filter_by(role="annachi").all()
    if not anns_all:
        return None, None

    # Phase 1: same pincode
    phase1 = []
    if consumer_pincode:
        phase1 = [a for a in anns_all if str(a.pincode or "").strip() == str(consumer_pincode).strip()]

    def score(ann):
        dist = _distance_km(consumer_lat, consumer_lng, ann)
        # enforce radius if GPS exists
        if (consumer_lat is not None and consumer_lng is not None) and isinstance(ann.service_radius_km, int):
            # if shop has coords; if no coords, allow as "unknown range"
            in_radius = True
            if dist < 10**8:
                in_radius = dist <= float(ann.service_radius_km or 0)
            if not in_radius:
                # put them at the end
                return (1, 10**9, ann.id)
        return (0, dist, ann.id)

    # Prefer phase1 if any viable exists (in radius when GPS provided)
    choice_pool = phase1 if phase1 else anns_all
    # Sort by (out-of-radius flag, distance, id)
    ranked = sorted(choice_pool, key=score)
    chosen = ranked[0] if ranked else None

    # If we chose an out-of-radius annachi (flag 1) AND we have GPS, try fallback to any in radius globally
    if chosen and consumer_lat is not None and consumer_lng is not None:
        # Check if chosen was out of radius
        ch_dist = _distance_km(consumer_lat, consumer_lng, chosen)
        if isinstance(chosen.service_radius_km, int) and ch_dist < 10**8 and ch_dist > float(chosen.service_radius_km or 0):
            # fallback: find any in radius overall
            inrad = []
            for a in anns_all:
                d = _distance_km(consumer_lat, consumer_lng, a)
                if d < 10**8 and isinstance(a.service_radius_km, int) and d <= float(a.service_radius_km or 0):
                    inrad.append((d, a.id, a))
            if inrad:
                inrad.sort(key=lambda t: (t[0], t[1]))
                chosen = inrad[0][2]

    if not chosen:
        return None, None
    dist = None
    if consumer_lat is not None and consumer_lng is not None:
        dd = _distance_km(consumer_lat, consumer_lng, chosen)
        if dd < 10**8:
            dist = round(dd, 3)
    return chosen, dist

@app.route("/api/nearest_annachi")
def api_nearest_annachi():
    """
    Query params: pin (optional), lat (optional), lng (optional)
    Returns chosen Annachi (single shop) the consumer should connect to.
    """
    pin = request.args.get("pin")
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    try:
        lat = float(lat) if lat is not None and lat != "" else None
    except:
        lat = None
    try:
        lng = float(lng) if lng is not None and lng != "" else None
    except:
        lng = None

    ann, dist = select_best_annachi(consumer_pincode=pin, consumer_lat=lat, consumer_lng=lng)
    if not ann:
        return jsonify({"error": "No Annachi available for this area"}), 404
    return jsonify({
        "ok": True,
        "annachi": {
            "id": ann.id,
            "email": ann.email,
            "pincode": ann.pincode,
            "shop_lat": ann.shop_lat,
            "shop_lng": ann.shop_lng,
            "service_radius_km": ann.service_radius_km
        },
        "distance_km": dist
    })

@app.route("/api/annachi/<int:annachi_id>/products")
def api_annachi_products(annachi_id):
    """
    List ONLY the products owned by a specific Annachi.
    Optional: ?category=cereals|fruits|vegetables or 'all'
    """
    cat = (request.args.get("category") or "all").strip().lower()
    if cat != "all" and cat not in FIXED_CATS:
        return jsonify([])

    q = Product.query.filter_by(owner_id=annachi_id)
    if cat != "all":
        q = q.filter_by(category=cat)
    products = q.order_by(Product.category, Product.name).all()
    return jsonify([
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "mrp": p.mrp,
            "price": p.price,
            "stock": p.stock,
            "pincode": p.pincode,
            "image_url": p.image_url,
            "total_purchased": p.total_purchased
        } for p in products
    ])


# ---------- ORDER APIs ----------
@app.route("/api/orders", methods=["POST"])
@login_required
def api_create_order():
    if current_user.role != "consumer":
        return jsonify({"error": "Only consumers can place orders"}), 403

    data = request.get_json(silent=True) or {}
    pid = data.get("product_id")
    qty = int(data.get("quantity") or 1)
    product = Product.query.get(pid)
    if not product:
        return jsonify({"error": "Product not found"}), 404
    if (product.category or "").lower() not in FIXED_CATS:
        return jsonify({"error": "Category not allowed"}), 400
    if qty < 1:
        return jsonify({"error": "Quantity must be >= 1"}), 400
    if product.stock < qty:
        return jsonify({"error": "Insufficient stock"}), 400

    bundle_id = f"{int(datetime.utcnow().timestamp()*1000)}-{current_user.id}-single"

    order = Order(
        consumer_id=current_user.id,
        product_id=product.id,
        quantity=qty,
        status="Pending",
        assigned_annachi_id=product.owner_id,
        bundle_id=bundle_id
    )
    product.stock -= qty
    product.total_purchased = (product.total_purchased or 0) + qty

    db.session.add(order)
    db.session.commit()

    try:
        broadcast({
            "type": "new_order",
            "owner_id": product.owner_id,
            "consumer_id": current_user.id,
            "assigned_annachi_id": order.assigned_annachi_id,
            "order": {
                "id": order.id,
                "created_at": order.created_at.isoformat(),
                "status": order.status,
                "quantity": order.quantity,
                "total": order.quantity * product.price,
                "product": {"name": product.name, "price": product.price, "category": product.category},
                "bundle_id": order.bundle_id
            }
        })
    except Exception as e:
        app.logger.warning(f"SSE broadcast failed: {e}")

    return jsonify({"ok": True, "order_id": order.id, "bundle_id": order.bundle_id})


@app.route("/orders/create", methods=["POST"])
@login_required
def orders_create():
    if current_user.role != "consumer":
        return jsonify({"error": "Only consumers can place orders"}), 403

    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "Cart is empty"}), 400

    bundle_id = f"{int(datetime.utcnow().timestamp()*1000)}-{current_user.id}-legacy"

    created = []
    for it in items:
        pid = int(it.get("product_id"))
        qty = int(it.get("quantity", 1))
        product = Product.query.get(pid)
        if not product:
            return jsonify({"error": "Product not found"}), 404
        if (product.category or "").lower() not in FIXED_CATS:
            return jsonify({"error": f"Category not allowed for {product.name}"}), 400
        if qty < 1:
            return jsonify({"error": "Quantity must be >= 1"}), 400
        if product.stock < qty:
            return jsonify({"error": f"Insufficient stock for {product.name}"}), 400

        order = Order(
            consumer_id=current_user.id,
            product_id=product.id,
            quantity=qty,
            status="Pending",
            assigned_annachi_id=product.owner_id,
            bundle_id=bundle_id
        )
        product.stock -= qty
        product.total_purchased = (product.total_purchased or 0) + qty
        db.session.add(order)
        created.append(order)

    db.session.commit()

    for order in created:
        try:
            broadcast({
                "type": "new_order",
                "owner_id": order.product.owner_id if order.product else None,
                "consumer_id": order.consumer_id,
                "assigned_annachi_id": order.assigned_annachi_id,
                "order": {
                    "id": order.id,
                    "created_at": order.created_at.isoformat(),
                    "status": order.status,
                    "quantity": order.quantity,
                    "total": order.quantity * (order.product.price if order.product else 0),
                    "product": {
                        "name": order.product.name if order.product else "",
                        "price": order.product.price if order.product else 0,
                        "category": order.product.category if order.product else ""
                    },
                    "bundle_id": order.bundle_id
                }
            })
        except Exception as e:
            app.logger.warning(f"SSE broadcast failed: {e}")

    bundle_ids = sorted(list({o.bundle_id for o in created if o.bundle_id}))
    return jsonify({"ok": True, "created": [o.id for o in created], "bundle_ids": bundle_ids})


# ---------- AUTO-ASSIGN ORDER ROUTE (SINGLE ANNACHI, NO SPLIT) ----------
@app.route("/orders/create_auto", methods=["POST"])
@login_required
def orders_create_auto():
    """
    Accepts:
    {
      "items": [
        {"name":"Tomato","category":"vegetables","quantity":2},
        {"name":"Rice","category":"cereals","quantity":1}
      ],
      "consumer": {"lat": 13.0827, "lng": 80.2707, "pincode":"600001"}
    }
    Behavior (updated):
      • Select exactly ONE Annachi using pin+GPS (pin preferred, then nearest in radius).
      • Validate all items are available from that Annachi only.
      • If any item fails, return 409 with an 'unavailable' list (no splitting).
      • If all ok, create ONE bundle assigned to that Annachi.
    """
    if current_user.role != "consumer":
        return jsonify({"error": "Only consumers can place orders"}), 403

    payload = request.get_json(silent=True) or {}
    items = payload.get("items", [])
    consumer_info = payload.get("consumer", {}) or {}
    if not items:
        return jsonify({"error": "Cart is empty"}), 400

    # Validate every item category is fixed
    normalized_items = []
    for it in items:
        name = (it.get("name") or "").strip()
        category = (it.get("category") or "").strip().lower()
        qty = int(it.get("quantity") or 1)
        if not name or category not in FIXED_CATS or qty < 1:
            return jsonify({"error": f"Invalid line item: {it}"}), 400
        normalized_items.append({"name": name, "category": category, "qty": qty})

    c_lat = consumer_info.get("lat")
    c_lng = consumer_info.get("lng")
    c_pin = consumer_info.get("pincode")
    try:
        c_lat = float(c_lat) if c_lat is not None else None
    except:
        c_lat = None
    try:
        c_lng = float(c_lng) if c_lng is not None else None
    except:
        c_lng = None

    # Pick exactly one Annachi
    ann, dist = select_best_annachi(consumer_pincode=c_pin, consumer_lat=c_lat, consumer_lng=c_lng)
    if not ann:
        return jsonify({"error": "No Annachi available for your area"}), 404

    # For each item, find that product *for this Annachi only*
    unavailable = []
    found_rows = []  # list of tuples (product, qty)
    for it in normalized_items:
        prod = (Product.query
                .filter_by(owner_id=ann.id, name=it["name"], category=it["category"], pincode=str(ann.pincode or ""))
                .first())
        if not prod:
            unavailable.append({"name": it["name"], "category": it["category"], "reason": "not_found_for_selected_annachi"})
            continue
        if prod.stock < it["qty"]:
            unavailable.append({"name": it["name"], "category": it["category"], "reason": f"insufficient_stock ({prod.stock} left)"})
            continue
        found_rows.append((prod, it["qty"]))

    if unavailable:
        # 409 Conflict → UI can let user switch shop
        return jsonify({
            "error": "Some items are not available from the selected Annachi",
            "assigned_annachi_id": ann.id,
            "annachi_pincode": ann.pincode,
            "distance_km": dist,
            "unavailable": unavailable
        }), 409

    # All OK → create ONE bundle for this Annachi
    created = []
    bundle_id = f"{int(datetime.utcnow().timestamp()*1000)}-{current_user.id}-A{ann.id}"
    try:
        for prod, qty in found_rows:
            order = Order(
                consumer_id=current_user.id,
                product_id=prod.id,
                quantity=qty,
                status="Pending",
                assigned_annachi_id=ann.id,
                bundle_id=bundle_id
            )
            # decrement stock and bump total purchased
            prod.stock -= qty
            prod.total_purchased = (prod.total_purchased or 0) + qty
            db.session.add(order)
            created.append(order)

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to create orders: {e}"}), 500

    # Broadcast per line (existing UI listens per-line)
    for order in created:
        try:
            p = order.product
            broadcast({
                "type": "new_order",
                "owner_id": p.owner_id if p else None,                  # legacy UI
                "assigned_annachi_id": order.assigned_annachi_id,        # new targeting
                "consumer_id": order.consumer_id,
                "order": {
                    "id": order.id,
                    "created_at": order.created_at.isoformat(),
                    "status": order.status,
                    "quantity": order.quantity,
                    "total": order.quantity * (p.price if p else 0),
                    "product": {"name": (p.name if p else ""), "price": (p.price if p else 0), "category": (p.category if p else "")},
                    "bundle_id": order.bundle_id
                }
            })
        except Exception as e:
            app.logger.warning(f"SSE broadcast failed: {e}")

    return jsonify({
        "ok": True,
        "created": [o.id for o in created],
        "bundle_id": bundle_id,
        "assigned_annachi_id": ann.id,
        "distance_km": dist
    })


# ================================
# 🔐🔐 ADMIN CATALOG ROUTES — FAN-OUT TO ALL ANNACHIS IN PINCODE 🔐🔐
# ================================
def require_admin():
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 403
    return None

def _admin_upsert_for_all_annachis_in_pin(*, pincode, name, category, mrp, price, stock=None, image_url=None):
    """
    Upsert a product for EVERY Annachi whose profile pincode == pincode.
    Identity per annachi: (owner_id, name, category, pincode).
    - Sets mrp/price/image for all.
    - Sets stock only if provided (seed/overwrite).
    """
    anns = User.query.filter_by(role="annachi", pincode=str(pincode)).all()
    if not anns:
        return {"ok": False, "error": f"No Annachi users found in pincode {pincode}"}

    updated_count = 0
    for a in anns:
        prod = (Product.query
                .filter_by(owner_id=a.id, name=name, category=category, pincode=str(pincode))
                .first())
        if prod:
            prod.mrp = float(mrp)
            prod.price = float(price)
            if image_url:
                prod.image_url = image_url
            if stock is not None:
                prod.stock = int(stock)
        else:
            prod = Product(
                name=name,
                category=category,
                mrp=float(mrp),
                price=float(price),
                stock=(int(stock) if stock is not None else 0),
                pincode=str(pincode),
                image_url=image_url,
                owner_id=a.id
            )
            db.session.add(prod)
        updated_count += 1

    db.session.commit()
    return {"ok": True, "count": updated_count}


@app.route("/admin/catalog/upsert", methods=["POST"])
def admin_catalog_upsert():
    """
    Fan-out behavior:
    - Admin upserts (name, category, pincode).
    - We replicate/update the item for EVERY Annachi whose user.pincode == pincode.
    - Each Annachi owns their row (so their dashboard shows it, and they can manage stock).
    """
    err = require_admin()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    category = (data.get("category") or "").strip().lower()
    pincode = (data.get("pincode") or "").strip()
    mrp = data.get("mrp")
    price = data.get("price")
    stock = data.get("stock")  # optional seed stock
    image_url = data.get("image_url") or None

    # Validate
    if not (name and category and pincode):
        return jsonify({"error": "name, category, pincode are required"}), 400
    if category not in FIXED_CATS:
        return jsonify({"error": "Category must be one of cereals/fruits/vegetables"}), 400
    try:
        mrp = float(mrp)
        price = float(price)
        stock = int(stock) if stock is not None else None
    except Exception:
        return jsonify({"error": "mrp, price must be numbers; stock must be integer if provided"}), 400
    if price > mrp:
        return jsonify({"error": "price cannot exceed mrp"}), 400
    if stock is not None and stock < 0:
        return jsonify({"error": "stock cannot be negative"}), 400

    res = _admin_upsert_for_all_annachis_in_pin(
        pincode=pincode, name=name, category=category,
        mrp=mrp, price=price, stock=stock, image_url=image_url
    )
    if not res.get("ok"):
        return jsonify(res), 400

    return jsonify({"ok": True, "affected_annachis": res["count"]})


@app.route("/admin/catalog/delete", methods=["POST"])
def admin_catalog_delete():
    """
    Delete by product_id (admin only).
    """
    err = require_admin()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    pid = data.get("product_id")
    try:
        pid = int(pid)
    except Exception:
        return jsonify({"error": "product_id required"}), 400

    prod = Product.query.get(pid)
    if not prod:
        return jsonify({"error": "Product not found"}), 404

    db.session.delete(prod)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/catalog/bulk_upsert", methods=["POST"])
def admin_catalog_bulk_upsert():
    """
    Fan-out in bulk:
    - pincode is supplied once for the batch body.
    - For each item, replicate/update across ALL Annachis with that pincode.
    - Identity per-annachi: (owner_id, name, category, pincode)
    """
    err = require_admin()
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    pincode = (payload.get("pincode") or "").strip()
    items = payload.get("items") or []

    if not (pincode and isinstance(items, list) and items):
        return jsonify({"error": "pincode and items[] are required"}), 400

    results = []
    for it in items:
        name = (it.get("name") or "").strip()
        category = (it.get("category") or "").strip().lower()
        mrp = it.get("mrp")
        price = it.get("price")
        stock = it.get("stock")
        image_url = it.get("image_url") or None

        if not (name and category):
            results.append({"name": name, "ok": False, "error": "name & category required"})
            continue
        if category not in FIXED_CATS:
            results.append({"name": name, "ok": False, "error": "invalid category"})
            continue
        try:
            mrp = float(mrp)
            price = float(price)
            stock = int(stock) if stock is not None else None
        except Exception:
            results.append({"name": name, "ok": False, "error": "bad mrp/price/stock"})
            continue
        if price > mrp:
            results.append({"name": name, "ok": False, "error": "price>mrp"})
            continue
        if stock is not None and stock < 0:
            results.append({"name": name, "ok": False, "error": "stock<0"})
            continue

        res = _admin_upsert_for_all_annachis_in_pin(
            pincode=pincode, name=name, category=category,
            mrp=mrp, price=price, stock=stock, image_url=image_url
        )
        results.append({"name": name, **res})

    return jsonify({"ok": True, "results": results})


@app.route("/admin/catalog/list", methods=["GET"])
def admin_catalog_list():
    err = require_admin()
    if err:
        return err

    pincode = (request.args.get("pincode") or "").strip()
    q = Product.query
    if pincode:
        q = q.filter_by(pincode=pincode)

    prods = q.order_by(Product.pincode, Product.category, Product.name).all()
    return jsonify([
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "mrp": p.mrp,
            "price": p.price,
            "stock": p.stock,
            "pincode": p.pincode,
            "image_url": p.image_url,
        }
        for p in prods
    ])


# ---------- ADMIN CATALOG UI PAGE ----------
@app.route('/admin/catalog/ui')
def admin_catalog_ui():
    return render_template('admin_catalog.html')


# ---------- INIT DB + SAFE MIGRATIONS ----------
def _table_exists(table):
    try:
        conn = sqlite3.connect(DB_PATH)  # ✅ use same DB path
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        ok = cur.fetchone() is not None
        conn.close()
        return ok
    except Exception:
        return False

def _column_exists(table, column):
    try:
        if not _table_exists(table):
            return False
        conn = sqlite3.connect(DB_PATH)  # ✅ use same DB path
        cur = conn.cursor()
        cur.execute(f'PRAGMA table_info("{table}")')
        cols = [r[1] for r in cur.fetchall()]
        conn.close()
        return column in cols
    except Exception:
        return False

def _safe_add_column(table, column, ddl):
    if not _table_exists(table):
        return
    if _column_exists(table, column):
        return
    try:
        conn = sqlite3.connect(DB_PATH)  # ✅ use same DB path
        cur = conn.cursor()
        cur.execute(f'ALTER TABLE "{table}" ADD COLUMN {ddl}')
        conn.commit()
        conn.close()
        print(f"[migrate] added: {table}.{column}")
    except Exception as e:
        print(f"[migrate] note: could not add {table}.{column}: {e}")

with app.app_context():
    db.create_all()
    _safe_add_column("user", "shop_lat", "REAL")
    _safe_add_column("user", "shop_lng", "REAL")
    _safe_add_column("user", "pincode", "VARCHAR(10)")
    _safe_add_column("user", "service_radius_km", "INTEGER DEFAULT 5")
    _safe_add_column("order", "assigned_annachi_id", "INTEGER")
    _safe_add_column("order", "bundle_id", "VARCHAR(64)")

# ✅ Health check for Render/Load Balancers
@app.route("/healthz")
def healthz():
    return "ok", 200


# ---------- RUN ----------
if __name__ == "__main__":
    # ✅ bind properly for local; Render will run via gunicorn (see Start Command)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=not bool(os.getenv("RENDER")))
