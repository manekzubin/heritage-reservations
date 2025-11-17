import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta, date
from dateutil.parser import parse as parse_date
import io, csv

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, 'reservations.db')
DATABASE_URL = os.environ.get('DATABASE_URL', f"sqlite:///{SQLITE_PATH}")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
bcrypt = Bcrypt(app)

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), default='staff')  # admin, manager, staff

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

class Property(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    hotels = db.relationship('Hotel', backref='property', lazy=True)

class Hotel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('property.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    city = db.Column(db.String(80))
    description = db.Column(db.Text)
    room_types = db.relationship('RoomType', backref='hotel', lazy=True)

class RoomType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    capacity = db.Column(db.Integer, default=2)
    price = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)  # number of identical rooms
    reservations = db.relationship('Reservation', backref='room_type', lazy=True)

class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_type_id = db.Column(db.Integer, db.ForeignKey('room_type.id'), nullable=False)
    source = db.Column(db.String(80), default='direct')  # direct, booking.com, makemytrip, etc.
    external_id = db.Column(db.String(200), nullable=True)
    guest_name = db.Column(db.String(200), nullable=False)
    guest_email = db.Column(db.String(200), nullable=False)
    check_in = db.Column(db.Date, nullable=False)
    check_out = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def overlaps(self, start, end):
        return not (self.check_out <= start or self.check_in >= end)

# Login loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def role_required(role):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role != role and current_user.role != 'admin':
                flash('Unauthorized', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return wrapped
    return decorator

# Utility: check availability across nights for a RoomType
def is_available(room_type, start_date, end_date):
    d = start_date
    while d < end_date:
        day_start = d
        day_end = d + timedelta(days=1)
        count = 0
        for r in room_type.reservations:
            if r.overlaps(day_start, day_end):
                count += 1
        if count >= room_type.quantity:
            return False, d
        d += timedelta(days=1)
    return True, None

# Routes
@app.route('/')
def index():
    props = Property.query.all()
    return render_template('index.html', properties=props)

@app.route('/property/<int:property_id>')
def property_view(property_id):
    prop = Property.query.get_or_404(property_id)
    return render_template('property.html', property=prop)

@app.route('/hotel/<int:hotel_id>')
def hotel_view(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    return render_template('hotel.html', hotel=hotel)

@app.route('/book/<int:room_type_id>', methods=['GET','POST'])
def book_room(room_type_id):
    rt = RoomType.query.get_or_404(room_type_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        try:
            check_in = parse_date(request.form.get('check_in')).date()
            check_out = parse_date(request.form.get('check_out')).date()
        except Exception:
            flash('Invalid date format', 'danger')
            return redirect(request.url)
        if check_in >= check_out:
            flash('Check-out must be after check-in', 'danger')
            return redirect(request.url)
        ok, blocked_day = is_available(rt, check_in, check_out)
        if not ok:
            flash(f'Not available on {blocked_day}', 'danger')
            return redirect(request.url)
        res = Reservation(room_type_id=rt.id, guest_name=name, guest_email=email, check_in=check_in, check_out=check_out, source='direct')
        db.session.add(res)
        db.session.commit()
        flash(f'Reservation confirmed (Ref: {res.id})', 'success')
        return redirect(url_for('index'))
    return render_template('book.html', room_type=rt)

# Calendar events for FullCalendar
@app.route('/api/calendar_events/<int:room_type_id>')
def api_calendar_events(room_type_id):
    rt = RoomType.query.get_or_404(room_type_id)
    events = []
    for r in rt.reservations:
        events.append({
            'id': r.id,
            'title': f"{r.guest_name} ({r.source})",
            'start': r.check_in.isoformat(),
            'end': r.check_out.isoformat(),
            'extendedProps': {
                'source': r.source
            }
        })
    # also add availability markers per day
    today = date.today()
    for i in range(0, 30):
        d = today + timedelta(days=i)
        day_start = d
        day_end = d + timedelta(days=1)
        count = sum(1 for r in rt.reservations if r.overlaps(day_start, day_end))
        available = max(0, rt.quantity - count)
        events.append({
            'id': f'avail-{d.isoformat()}',
            'title': f'Available: {available}',
            'start': d.isoformat(),
            'allDay': True,
            'display': 'background' if available==0 else 'auto',
            'extendedProps': {'available': available}
        })
    return jsonify(events)

# Admin dashboard and reporting
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    props = Property.query.all()
    # simple filters via query params
    q = Reservation.query.join(RoomType).join(Hotel).join(Property)
    property_id = request.args.get('property_id', type=int)
    hotel_id = request.args.get('hotel_id', type=int)
    room_type_id = request.args.get('room_type_id', type=int)
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    if property_id:
        q = q.filter(Property.id == property_id)
    if hotel_id:
        q = q.filter(Hotel.id == hotel_id)
    if room_type_id:
        q = q.filter(RoomType.id == room_type_id)
    if date_from:
        df = parse_date(date_from).date()
        q = q.filter(Reservation.check_out > df)
    if date_to:
        dt = parse_date(date_to).date()
        q = q.filter(Reservation.check_in < dt)
    reservations = q.order_by(Reservation.created_at.desc()).all()
    if request.args.get('export') == 'csv':
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['id','source','external_id','guest_name','guest_email','property','hotel','room_type','check_in','check_out','created_at'])
        for r in reservations:
            cw.writerow([r.id, r.source, r.external_id, r.guest_name, r.guest_email, r.room_type.hotel.property.name, r.room_type.hotel.name, r.room_type.name, r.check_in, r.check_out, r.created_at])
        mem = io.BytesIO()
        mem.write(si.getvalue().encode('utf-8'))
        mem.seek(0)
        return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='reservations.csv')
    return render_template('admin_dashboard.html', properties=props, reservations=reservations)

# Simple OTA webhook receiver (idempotent)
@app.route('/ota/webhook', methods=['POST'])
def ota_webhook():
    data = request.get_json()
    required = ['ota','external_id','room_type_id','guest_name','guest_email','check_in','check_out']
    if not data or any(k not in data for k in required):
        return jsonify({'error':'missing fields'}), 400
    existing = Reservation.query.filter_by(source=data['ota'], external_id=data['external_id']).first()
    if existing:
        return jsonify({'status':'duplicate','reservation_id': existing.id}), 200
    rt = RoomType.query.get(data['room_type_id'])
    if not rt:
        return jsonify({'error':'invalid room_type'}), 400
    try:
        check_in = parse_date(data['check_in']).date()
        check_out = parse_date(data['check_out']).date()
    except Exception:
        return jsonify({'error':'invalid dates'}), 400
    ok, blocked = is_available(rt, check_in, check_out)
    if not ok:
        return jsonify({'status':'rejected','reason': f'No availability on {blocked}'}), 409
    res = Reservation(room_type_id=rt.id, source=data['ota'], external_id=data['external_id'], guest_name=data['guest_name'], guest_email=data['guest_email'], check_in=check_in, check_out=check_out)
    db.session.add(res)
    db.session.commit()
    return jsonify({'status':'accepted','reservation_id': res.id}), 201

# Reconciliation view (detect overbooked nights)
@app.route('/admin/reconcile')
@login_required
def admin_reconcile():
    conflicts = []
    rts = RoomType.query.all()
    for rt in rts:
        date_map = {}
        for r in rt.reservations:
            d = r.check_in
            while d < r.check_out:
                date_map.setdefault(d, []).append(r)
                d += timedelta(days=1)
        for d, lst in date_map.items():
            if len(lst) > rt.quantity:
                conflicts.append({'room_type': rt, 'date': d, 'count': len(lst), 'reservations': lst})
    return render_template('admin_reconcile.html', conflicts=conflicts)

# Auth routes
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip()
        password = request.form.get('password','').strip()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Logged in', 'success')
            return redirect(url_for('admin_dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    flash('Logged out', 'success')
    return redirect(url_for('index'))

# Admin - users
@app.route('/admin/users', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_users():
    if request.method == 'POST':
        email = request.form.get('email','').strip()
        password = request.form.get('password','').strip()
        role = request.form.get('role','staff')
        if not email or not password:
            flash('Email & password required', 'danger')
            return redirect(request.url)
        if User.query.filter_by(email=email).first():
            flash('User exists', 'danger')
            return redirect(request.url)
        u = User(email=email, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash('User created', 'success')
    users = User.query.order_by(User.id.desc()).all()
    return render_template('admin_users.html', users=users)

# Simple OTA availability push simulation
@app.route('/ota/push_availability/<string:ota_name>/<int:room_type_id>')
@login_required
def ota_push_availability(ota_name, room_type_id):
    rt = RoomType.query.get_or_404(room_type_id)
    snapshot = []
    today = date.today()
    for i in range(0,30):
        d = today + timedelta(days=i)
        count = sum(1 for r in rt.reservations if r.overlaps(d, d+timedelta(days=1)))
        available = max(0, rt.quantity - count)
        snapshot.append({'date': d.isoformat(), 'available': available})
    return render_template('ota_sync.html', ota=ota_name, room_type=rt, snapshot=snapshot)

# -------------------------
# Auto-seed on first run (useful for Render free tier where shell is not available)
# -------------------------
with app.app_context():
    try:
        # create tables if missing
        db.create_all()
    except Exception as e:
        # If DB backend not available (e.g., DATABASE_URL wrong), log and continue
        print("Warning: db.create_all() failed during startup:", str(e))

    # Seed only if no properties exist
    try:
        if Property.query.count() == 0:
            print("Auto-seeding database with sample data...")
            p = Property(name='Heritage Group of Hospitality')
            db.session.add(p)
            db.session.commit()

            h1 = Hotel(property_id=p.id, name='Kutch Heritage', city='Bhuj', description='A heritage stay in Kutch')
            h2 = Hotel(property_id=p.id, name='Heritage Palace', city='Bhuj', description='Comfort and tradition')
            db.session.add_all([h1, h2])
            db.session.commit()

            rt1 = RoomType(hotel_id=h1.id, name='Deluxe Double', capacity=2, price=2500, quantity=5)
            rt2 = RoomType(hotel_id=h1.id, name='Family Suite', capacity=4, price=4500, quantity=2)
            rt3 = RoomType(hotel_id=h2.id, name='Standard Room', capacity=2, price=2000, quantity=10)
            db.session.add_all([rt1, rt2, rt3])
            db.session.commit()

            admin = User(email='admin@heritage.local', role='admin')
            admin.set_password('password123')
            db.session.add(admin)
            db.session.commit()
            print('Auto-seed complete: admin@heritage.local / password123')
    except Exception as e:
        # Log seed errors without stopping the app
        print("Auto-seed skipped/failed:", str(e))

if __name__ == '__main__':
    # For local dev: keep previous behavior of creating DB if using local sqlite
    if not os.path.exists(SQLITE_PATH) and 'DATABASE_URL' not in os.environ:
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
