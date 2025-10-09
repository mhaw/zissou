import argparse
import os
import firebase_admin
from firebase_admin import credentials, auth

def set_admin_claim(email: str, is_admin: bool):
    """Sets or unsets the 'admin' custom claim for a Firebase user."""
    try:
        # Initialize Firebase Admin SDK if not already initialized
        if not firebase_admin._apps:
            # Use GOOGLE_APPLICATION_CREDENTIALS environment variable
            # or default credentials if running in GCP
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred)

        user = auth.get_user_by_email(email)
        current_claims = user.custom_claims or {}

        if is_admin:
            if current_claims.get('admin') == True:
                print(f"User {email} already has admin claim. No change needed.")
                return
            custom_claims = {**current_claims, 'admin': True}
            auth.set_custom_user_claims(user.uid, custom_claims)
            print(f"Successfully set admin claim for user: {email}")
        else:
            if current_claims.get('admin') != True:
                print(f"User {email} does not have admin claim. No change needed.")
                return
            custom_claims = {k: v for k, v in current_claims.items() if k != 'admin'}
            auth.set_custom_user_claims(user.uid, custom_claims)
            print(f"Successfully removed admin claim for user: {email}")

    except auth.UserNotFoundError:
        print(f"Error: User with email {email} not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set or unset admin claim for a Firebase user.")
    parser.add_argument("--email", required=True, help="Email of the user.")
    parser.add_argument("--admin", action="store_true", help="Set admin claim to true.")
    parser.add_argument("--no-admin", action="store_true", help="Set admin claim to false (remove admin claim).")

    args = parser.parse_args()

    if args.admin and args.no_admin:
        parser.error("Cannot specify both --admin and --no-admin.")
    if not args.admin and not args.no_admin:
        parser.error("Must specify either --admin or --no-admin.")

    set_admin_claim(args.email, args.admin)
