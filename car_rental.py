
import hashlib
import os
import sqlite3
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATABASE_NAME = BASE_DIR / "car_rental.db"
INSURANCE_PER_DAY = 15.00
GPS_FLAT_FEE = 20.00


def connect_database():
    connection = sqlite3.connect(DATABASE_NAME)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 100_000
    )
    return salt.hex(), password_hash.hex()


def verify_password(password, stored_salt, stored_hash):
    _, derived_password_hash = hash_password(password, bytes.fromhex(stored_salt))
    return derived_password_hash == stored_hash


def initialise_database():
    with connect_database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('customer', 'admin'))
            );

            CREATE TABLE IF NOT EXISTS cars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                make TEXT NOT NULL,
                model TEXT NOT NULL,
                year INTEGER NOT NULL,
                mileage INTEGER NOT NULL,
                available_now INTEGER NOT NULL DEFAULT 1,
                min_rent_days INTEGER NOT NULL,
                max_rent_days INTEGER NOT NULL,
                daily_rate REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                car_id INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                rental_days INTEGER NOT NULL,
                phone TEXT NOT NULL,
                licence_number TEXT NOT NULL,
                insurance INTEGER NOT NULL DEFAULT 0,
                gps INTEGER NOT NULL DEFAULT 0,
                total_fee REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'approved', 'rejected')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(customer_id) REFERENCES users(id),
                FOREIGN KEY(car_id) REFERENCES cars(id)
            );
            """
        )

        admin_record = connection.execute(
            "SELECT 1 FROM users WHERE username = ?",
            ("admin",),
        ).fetchone()
        if admin_record is None:
            salt_value, password_hash_value = hash_password("admin123")
            connection.execute(
                """INSERT INTO users
                   (full_name, username, password_salt, password_hash, role)
                   VALUES (?, ?, ?, ?, 'admin')""",
                ("System Admin", "admin", salt_value, password_hash_value),
            )

        car_count = connection.execute("SELECT COUNT(*) FROM cars").fetchone()[0]
        if car_count == 0:
            starter_cars = [
                ("Toyota", "Corolla", 2022, 42000, 1, 1, 30, 75.00),
                ("Mazda", "CX-5", 2023, 28000, 1, 2, 21, 110.00),
                ("Honda", "Fit", 2021, 51000, 1, 1, 14, 65.00),
            ]
            connection.executemany(
                """INSERT INTO cars
                   (make, model, year, mileage, available_now,
                    min_rent_days, max_rent_days, daily_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                starter_cars,
            )


def read_integer(prompt, minimum=None):
    while True:
        try:
            entered_value = int(input(prompt).strip())
            if minimum is not None and entered_value < minimum:
                print(f"Please enter a number of at least {minimum}.")
                continue
            return entered_value
        except ValueError:
            print("Please enter a valid whole number.")


def read_money(prompt):
    while True:
        try:
            entered_value = float(input(prompt).strip())
            if entered_value <= 0:
                print("The amount must be greater than zero.")
                continue
            return entered_value
        except ValueError:
            print("Please enter a valid amount.")


def read_yes_no(prompt):
    while True:
        answer = input(prompt).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please enter y or n.")


def read_date(prompt):
    while True:
        date_text = input(prompt).strip()
        try:
            return datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            print("Use the format YYYY-MM-DD, for example 2026-09-20.")


def register_customer():
    print("\n--- Customer Registration ---")
    full_name = input("Full name: ").strip()
    username = input("Choose a username: ").strip().lower()
    password = input("Choose a password: ").strip()

    if not full_name or not username or len(password) < 6:
        print("Complete all fields. The password needs at least 6 characters.")
        return

    salt_value, password_hash_value = hash_password(password)
    try:
        with connect_database() as connection:
            connection.execute(
                """INSERT INTO users
                   (full_name, username, password_salt, password_hash, role)
                   VALUES (?, ?, ?, ?, 'customer')""",
                (full_name, username, salt_value, password_hash_value),
            )
        print("Registration successful. You can now log in.")
    except sqlite3.IntegrityError:
        print("That username already exists. Please choose another one.")


def login_user():
    print("\n--- Login ---")
    username = input("Username: ").strip().lower()
    password = input("Password: ").strip()

    with connect_database() as connection:
        user_record = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if user_record and verify_password(
        password,
        user_record["password_salt"],
        user_record["password_hash"],
    ):
        print(f"Welcome, {user_record['full_name']}!")
        return user_record

    print("Incorrect username or password.")
    return None


def display_cars(car_list):
    """Print a simple table of available cars."""
    if not car_list:
        print("No cars found.")
        return

    print("\nID  Car                       Year  Mileage  Min-Max days  Daily rate")
    print("-" * 72)
    for car in car_list:
        car_name = f"{car['make']} {car['model']}"
        print(
            f"{car['id']:<3} {car_name:<25} {car['year']:<5} "
            f"{car['mileage']:<8} {car['min_rent_days']}-{car['max_rent_days']:<11} "
            f"${car['daily_rate']:.2f}"
        )


def view_available_cars():
    with connect_database() as connection:
        cars = connection.execute(
            "SELECT * FROM cars WHERE available_now = 1 ORDER BY id"
        ).fetchall()
    display_cars(cars)


def booking_overlaps(connection, car_id, start_date, end_date, exclude_booking_id=None):
    query = """
        SELECT id FROM bookings
        WHERE car_id = ?
          AND status IN ('pending', 'approved')
          AND start_date <= ?
          AND end_date >= ?
    """
    values = [car_id, end_date.isoformat(), start_date.isoformat()]
    if exclude_booking_id is not None:
        query += " AND id != ?"
        values.append(exclude_booking_id)
    return connection.execute(query, values).fetchone() is not None


def calculate_booking_cost(car_record, rental_day_count, includes_insurance, includes_gps):
    base_charge = car_record["daily_rate"] * rental_day_count
    insurance_charge = INSURANCE_PER_DAY * rental_day_count if includes_insurance else 0.0
    gps_charge = GPS_FLAT_FEE if includes_gps else 0.0
    total_charge = base_charge + insurance_charge + gps_charge
    return base_charge, insurance_charge, gps_charge, total_charge


def create_booking_for_customer(customer_record):
    view_available_cars()
    selected_car_id = read_integer("\nEnter the car ID to book: ", 1)
    start_date = read_date("Start date (YYYY-MM-DD): ")
    end_date = read_date("End date (YYYY-MM-DD): ")

    if end_date < start_date:
        print("The end date cannot be before the start date.")
        return

    rental_day_count = (end_date - start_date).days + 1
    phone_number = input("Phone number: ").strip()
    licence_number = input("Driver licence number: ").strip()
    includes_insurance = read_yes_no(
        f"Add insurance (${INSURANCE_PER_DAY:.2f} per day)? (y/n): "
    )
    includes_gps = read_yes_no(f"Add GPS (${GPS_FLAT_FEE:.2f} once)? (y/n): ")

    if not phone_number or not licence_number:
        print("Phone number and driver licence number are required.")
        return

    with connect_database() as connection:
        selected_car = connection.execute(
            "SELECT * FROM cars WHERE id = ? AND available_now = 1",
            (selected_car_id,),
        ).fetchone()

        if not selected_car:
            print("That car is not available.")
            return
        if not selected_car["min_rent_days"] <= rental_day_count <= selected_car["max_rent_days"]:
            print(
                f"This car must be rented for {selected_car['min_rent_days']} to "
                f"{selected_car['max_rent_days']} days."
            )
            return
        if booking_overlaps(connection, selected_car_id, start_date, end_date):
            print("This car already has a booking request for those dates.")
            return

        base_charge, insurance_charge, gps_charge, total_charge = calculate_booking_cost(
            selected_car,
            rental_day_count,
            includes_insurance,
            includes_gps,
        )
        print("\n--- Fee Summary ---")
        print(f"Base rental: ${base_charge:.2f}")
        print(f"Insurance:   ${insurance_charge:.2f}")
        print(f"GPS:         ${gps_charge:.2f}")
        print(f"Total:       ${total_charge:.2f}")

        if not read_yes_no("Submit this booking request? (y/n): "):
            print("Booking cancelled.")
            return

        connection.execute(
            """INSERT INTO bookings
               (customer_id, car_id, start_date, end_date, rental_days,
                phone, licence_number, insurance, gps, total_fee)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                customer_record["id"],
                selected_car_id,
                start_date.isoformat(),
                end_date.isoformat(),
                rental_day_count,
                phone_number,
                licence_number,
                int(includes_insurance),
                int(includes_gps),
                total_charge,
            ),
        )
    print("Booking request submitted. An admin must approve it.")


def view_customer_bookings(customer_id):
    with connect_database() as connection:
        bookings = connection.execute(
            """SELECT b.*, c.make, c.model
               FROM bookings b JOIN cars c ON b.car_id = c.id
               WHERE b.customer_id = ? ORDER BY b.id DESC""",
            (customer_id,),
        ).fetchall()

    if not bookings:
        print("You have no bookings.")
        return

    print("\n--- My Bookings ---")
    for booking in bookings:
        print(
            f"#{booking['id']} | {booking['make']} {booking['model']} | "
            f"{booking['start_date']} to {booking['end_date']} | "
            f"${booking['total_fee']:.2f} | {booking['status'].upper()}"
        )


def add_car_record():
    print("\n--- Add Car ---")
    make_name = input("Make: ").strip()
    model_name = input("Model: ").strip()
    year_value = read_integer("Year: ", 1900)
    mileage_value = read_integer("Mileage: ", 0)
    minimum_days = read_integer("Minimum rental days: ", 1)
    maximum_days = read_integer("Maximum rental days: ", minimum_days)
    daily_rent = read_money("Daily rate: $")

    if not make_name or not model_name:
        print("Make and model are required.")
        return

    with connect_database() as connection:
        connection.execute(
            """INSERT INTO cars
               (make, model, year, mileage, available_now,
                min_rent_days, max_rent_days, daily_rate)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            (make_name, model_name, year_value, mileage_value, minimum_days, maximum_days, daily_rent),
        )
    print("Car added successfully.")


def update_car_record():
    with connect_database() as connection:
        cars = connection.execute("SELECT * FROM cars ORDER BY id").fetchall()
    display_cars(cars)
    selected_car_id = read_integer("Enter the car ID to update: ", 1)

    with connect_database() as connection:
        car_record = connection.execute(
            "SELECT * FROM cars WHERE id = ?",
            (selected_car_id,),
        ).fetchone()
        if not car_record:
            print("Car not found.")
            return

        print("Press Enter to keep the current text or number.")
        make_name = input(f"Make [{car_record['make']}]: ").strip() or car_record["make"]
        model_name = input(f"Model [{car_record['model']}]: ").strip() or car_record["model"]
        try:
            year_value = int(input(f"Year [{car_record['year']}]: ").strip() or car_record["year"])
            mileage_value = int(input(f"Mileage [{car_record['mileage']}]: ").strip() or car_record["mileage"])
            minimum_days = int(input(f"Minimum days [{car_record['min_rent_days']}]: ").strip() or car_record["min_rent_days"])
            maximum_days = int(input(f"Maximum days [{car_record['max_rent_days']}]: ").strip() or car_record["max_rent_days"])
            daily_rent = float(input(f"Daily rate [{car_record['daily_rate']}]: ").strip() or car_record["daily_rate"])
        except ValueError:
            print("Update cancelled because a number was invalid.")
            return

        is_available = read_yes_no("Is the car available now? (y/n): ")

        if (
            year_value < 1900
            or mileage_value < 0
            or minimum_days < 1
            or maximum_days < minimum_days
            or daily_rent <= 0
        ):
            print("Update cancelled because one or more values are outside the allowed range.")
            return

        connection.execute(
            """UPDATE cars SET make = ?, model = ?, year = ?, mileage = ?,
               available_now = ?, min_rent_days = ?, max_rent_days = ?, daily_rate = ?
               WHERE id = ?""",
            (
                make_name,
                model_name,
                year_value,
                mileage_value,
                int(is_available),
                minimum_days,
                maximum_days,
                daily_rent,
                selected_car_id,
            ),
        )
    print("Car updated successfully.")


def delete_car_record():
    with connect_database() as connection:
        cars = connection.execute("SELECT * FROM cars ORDER BY id").fetchall()
    display_cars(cars)
    selected_car_id = read_integer("Enter the car ID to delete: ", 1)

    with connect_database() as connection:
        car_record = connection.execute(
            "SELECT * FROM cars WHERE id = ?",
            (selected_car_id,),
        ).fetchone()
        if not car_record:
            print("Car not found.")
            return

        booking_count = connection.execute(
            "SELECT COUNT(*) FROM bookings WHERE car_id = ?",
            (selected_car_id,),
        ).fetchone()[0]
        if booking_count:
            print("This car has booking history, so it cannot be deleted. Mark it unavailable instead.")
            return

        if read_yes_no(f"Delete {car_record['make']} {car_record['model']}? (y/n): "):
            connection.execute("DELETE FROM cars WHERE id = ?", (selected_car_id,))
            print("Car deleted successfully.")
        else:
            print("Delete cancelled.")


def view_all_cars():
    with connect_database() as connection:
        cars = connection.execute("SELECT * FROM cars ORDER BY id").fetchall()
    display_cars(cars)
    if cars:
        print("\nAvailability:")
        for car in cars:
            availability_status = "Available" if car["available_now"] else "Unavailable"
            print(f"Car {car['id']}: {availability_status}")


def view_all_bookings():
    with connect_database() as connection:
        bookings = connection.execute(
            """SELECT b.*, u.full_name, c.make, c.model
               FROM bookings b
               JOIN users u ON b.customer_id = u.id
               JOIN cars c ON b.car_id = c.id
               ORDER BY CASE b.status WHEN 'pending' THEN 0 ELSE 1 END, b.id DESC"""
        ).fetchall()

    if not bookings:
        print("No bookings found.")
        return

    print("\n--- All Bookings ---")
    for booking in bookings:
        print(
            f"#{booking['id']} | {booking['full_name']} | "
            f"{booking['make']} {booking['model']} | "
            f"{booking['start_date']} to {booking['end_date']} | "
            f"${booking['total_fee']:.2f} | {booking['status'].upper()}"
        )
        print(f"   Phone: {booking['phone']} | Licence: {booking['licence_number']}")


def update_booking_status(new_status):
    view_all_bookings()
    selected_booking_id = read_integer(f"Enter booking ID to {new_status}: ", 1)

    with connect_database() as connection:
        booking_record = connection.execute(
            "SELECT * FROM bookings WHERE id = ?",
            (selected_booking_id,),
        ).fetchone()
        if not booking_record:
            print("Booking not found.")
            return
        if booking_record["status"] != "pending":
            print("Only pending bookings can be changed.")
            return

        if new_status == "approved":
            car_record = connection.execute(
                "SELECT available_now FROM cars WHERE id = ?",
                (booking_record["car_id"],),
            ).fetchone()
            start_date = datetime.strptime(booking_record["start_date"], "%Y-%m-%d").date()
            end_date = datetime.strptime(booking_record["end_date"], "%Y-%m-%d").date()
            if not car_record or not car_record["available_now"]:
                print("The car is currently marked unavailable, so approval was stopped.")
                return
            if booking_overlaps(
                connection,
                booking_record["car_id"],
                start_date,
                end_date,
                selected_booking_id,
            ):
                print("Another pending or approved booking overlaps these dates.")
                return

        connection.execute(
            "UPDATE bookings SET status = ? WHERE id = ?",
            (new_status, selected_booking_id),
        )
    print(f"Booking {new_status} successfully.")


def customer_menu(customer_record):
    while True:
        print("\n--- Customer Menu ---")
        print("1. View available cars")
        print("2. Book a car")
        print("3. View my bookings")
        print("4. Log out")
        choice = input("Choose an option: ").strip()

        if choice == "1":
            view_available_cars()
        elif choice == "2":
            create_booking_for_customer(customer_record)
        elif choice == "3":
            view_customer_bookings(customer_record["id"])
        elif choice == "4":
            break
        else:
            print("Please choose 1, 2, 3, or 4.")


def admin_menu():
    while True:
        print("\n--- Admin Menu ---")
        print("1. View all cars")
        print("2. Add a car")
        print("3. Update a car")
        print("4. Delete a car")
        print("5. View all bookings")
        print("6. Approve a booking")
        print("7. Reject a booking")
        print("8. Log out")
        choice = input("Choose an option: ").strip()

        if choice == "1":
            view_all_cars()
        elif choice == "2":
            add_car_record()
        elif choice == "3":
            update_car_record()
        elif choice == "4":
            delete_car_record()
        elif choice == "5":
            view_all_bookings()
        elif choice == "6":
            update_booking_status("approved")
        elif choice == "7":
            update_booking_status("rejected")
        elif choice == "8":
            break
        else:
            print("Please choose a number from 1 to 8.")


def main():
    initialise_database()
    print("Car Rental System")
    print("Starter admin login: admin / admin123")

    while True:
        print("\n--- Main Menu ---")
        print("1. Register as a customer")
        print("2. Log in")
        print("3. Exit")
        choice = input("Choose an option: ").strip()

        if choice == "1":
            register_customer()
        elif choice == "2":
            user_record = login_user()
            if user_record:
                if user_record["role"] == "admin":
                    admin_menu()
                else:
                    customer_menu(user_record)
        elif choice == "3":
            print("Thank you for using the Car Rental System.")
            break
        else:
            print("Please choose 1, 2, or 3.")


if __name__ == "__main__":
    main()
