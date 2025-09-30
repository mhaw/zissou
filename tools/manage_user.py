import os
import argparse
from google.cloud import firestore
from dotenv import load_dotenv


def get_user_by_email(db, email):
    users_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_USERS", "users"))
    query = users_ref.where("email", "==", email).limit(1)
    docs = list(query.stream())
    if not docs:
        return None
    return docs[0]


def set_user_role(email, role):
    """Finds a user by email and sets their role."""
    load_dotenv()
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    if not project_id:
        print("Error: FIREBASE_PROJECT_ID environment variable not set.")
        return

    db = firestore.Client(project=project_id)

    print(f"Searching for user with email: {email}...")
    user_doc = get_user_by_email(db, email)

    if not user_doc:
        print(f"Error: User with email {email} not found.")
        return

    user_ref = user_doc.reference
    user_ref.update({"role": role})
    print(f"Successfully updated role for {email} to '{role}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage user roles.")
    parser.add_argument("--email", required=True, help="Email of the user to update.")
    parser.add_argument(
        "--role", required=True, help="The role to assign (e.g., admin, member)."
    )
    args = parser.parse_args()

    set_user_role(args.email, args.role)
