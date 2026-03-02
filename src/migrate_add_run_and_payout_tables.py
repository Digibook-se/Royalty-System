#!/usr/bin/env python3
from models import Base, engine

def main():
    print(">>> Skapar saknade tabeller (om de inte finns)...")
    Base.metadata.create_all(engine)
    print("✅ Klart. (create_all är säker: den skapar bara tabeller som saknas)")

if __name__ == "__main__":
    main()
