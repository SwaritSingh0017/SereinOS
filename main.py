from src.database.database import Database


def extract_name(text):

    text = text.strip()

    prefixes = [
        "my name is",
        "i am",
        "i'm"
    ]

    lower_text = text.lower()

    for prefix in prefixes:

        if lower_text.startswith(prefix):

            return text[len(prefix):].strip()

    return text


db = Database()

identity = db.get_identity()

print("\nSereinOS\n")

if identity is None:

    print("Serein: Hello.")
    print("Serein: I'm Serein.")
    print("Serein: I think this is our first time meeting.")

    user_input = input(
        "Serein: May I know your name?\n> "
    )

    user_name = extract_name(user_input)

    db.create_identity(user_name)

    db.save_memory(
        content=f"User's name is {user_name}",
        tags="identity,name",
        importance=100,
        emotion="neutral",
        source="text"
    )

    print(f"\nSerein: It's nice to meet you, {user_name}.")

else:

    display_name = db.get_display_name()

    db.update_last_seen()

    print(
        f"Serein: Welcome back, {display_name}."
    )

db.close()