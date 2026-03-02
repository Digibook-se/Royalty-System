from models import SessionLocal, Author

AUTHORS_TO_ADD = [
    {
        "name": "Håkan",
        "email": "hakan@example.com",
        "bank_account": None,
    },
    {
        "name": "Langlé, Annika",
        "email": "annika@example.com",
        "bank_account": None,
    },
]

def main():
    db = SessionLocal()
    created = 0

    for a in AUTHORS_TO_ADD:
        exists = db.query(Author).filter_by(email=a["email"]).first()
        if exists:
            print(f"Finns redan: {a['email']}")
            continue

        author = Author(
            name=a["name"],
            email=a["email"],
            bank_account=a["bank_account"],
            carried_balance=0,
        )
        db.add(author)
        created += 1

    db.commit()
    db.close()
    print(f"Skapade {created} författare")

if __name__ == "__main__":
    main()
