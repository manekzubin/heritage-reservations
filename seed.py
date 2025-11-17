from app import db, Property, Hotel, RoomType, User

def seed():
    db.create_all()
    if Property.query.count() > 0:
        print('Already seeded')
        return
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

    # admin user
    admin = User(email='admin@heritage.local', role='admin')
    admin.set_password('password123')
    db.session.add(admin)
    db.session.commit()
    print('Seeded sample data and created admin user (admin@heritage.local / password123)')

if __name__ == '__main__':
    seed()
