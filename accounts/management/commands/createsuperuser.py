import getpass
import os
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.management.commands.createsuperuser import (
    Command as DjangoCreateSuperuserCommand,
)
from django.contrib.auth.management.commands.createsuperuser import (
    NotRunningInTTYException,
    PASSWORD_FIELD,
)
from django.core import exceptions
from django.core.management.base import CommandError
from django.utils.text import capfirst


class Command(DjangoCreateSuperuserCommand):
    help = "Used to create a superuser linked to a Person."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--person-first-name",
            dest="person_first_name",
            help="Specifies the first name for the linked person.",
        )
        parser.add_argument(
            "--person-last-name",
            dest="person_last_name",
            help="Specifies the last name for the linked person.",
        )

    def handle(self, *args, **options):
        username = options[self.UserModel.USERNAME_FIELD]
        database = options["database"]
        user_data = {}
        verbose_field_name = self.username_field.verbose_name
        try:
            self.UserModel._meta.get_field(PASSWORD_FIELD)
        except exceptions.FieldDoesNotExist:
            pass
        else:
            user_data[PASSWORD_FIELD] = None

        try:
            if options["interactive"]:
                fake_user_data = {}
                if hasattr(self.stdin, "isatty") and not self.stdin.isatty():
                    raise NotRunningInTTYException

                if username:
                    error_msg = self._validate_username(
                        username, verbose_field_name, database
                    )
                    if error_msg:
                        self.stderr.write(error_msg)
                        username = None
                elif username == "":
                    raise CommandError(
                        "%s cannot be blank." % capfirst(verbose_field_name)
                    )

                while username is None:
                    message = self._get_input_message(self.username_field)
                    username = self.get_input_data(self.username_field, message)
                    if username:
                        error_msg = self._validate_username(
                            username, verbose_field_name, database
                        )
                        if error_msg:
                            self.stderr.write(error_msg)
                            username = None
                            continue

                user_data[self.UserModel.USERNAME_FIELD] = username
                fake_user_data[self.UserModel.USERNAME_FIELD] = username

                user_data["person_first_name"] = self._prompt_non_model_field(
                    options.get("person_first_name"),
                    "Person first name: ",
                )
                user_data["person_last_name"] = self._prompt_non_model_field(
                    options.get("person_last_name"),
                    "Person last name: ",
                )

                while PASSWORD_FIELD in user_data and user_data[PASSWORD_FIELD] is None:
                    password = getpass.getpass()
                    password2 = getpass.getpass("Password (again): ")
                    if password != password2:
                        self.stderr.write("Error: Your passwords didn't match.")
                        continue
                    if password.strip() == "":
                        self.stderr.write("Error: Blank passwords aren't allowed.")
                        continue
                    try:
                        validate_target = get_user_model()(**fake_user_data)
                        from django.contrib.auth.password_validation import validate_password

                        validate_password(password2, validate_target)
                    except exceptions.ValidationError as err:
                        self.stderr.write("\n".join(err.messages))
                        response = input(
                            "Bypass password validation and create user anyway? [y/N]: "
                        )
                        if response.lower() != "y":
                            continue
                    user_data[PASSWORD_FIELD] = password
            else:
                if (
                    PASSWORD_FIELD in user_data
                    and "DJANGO_SUPERUSER_PASSWORD" in os.environ
                ):
                    user_data[PASSWORD_FIELD] = os.environ["DJANGO_SUPERUSER_PASSWORD"]

                if username is None:
                    username = os.environ.get(
                        "DJANGO_SUPERUSER_" + self.UserModel.USERNAME_FIELD.upper()
                    )
                if username is None:
                    raise CommandError(
                        "You must use --%s with --noinput."
                        % self.UserModel.USERNAME_FIELD
                    )

                error_msg = self._validate_username(
                    username, verbose_field_name, database
                )
                if error_msg:
                    raise CommandError(error_msg)

                user_data[self.UserModel.USERNAME_FIELD] = username
                user_data["person_first_name"] = self._get_noninteractive_value(
                    options,
                    "person_first_name",
                    "DJANGO_SUPERUSER_PERSON_FIRST_NAME",
                )
                user_data["person_last_name"] = self._get_noninteractive_value(
                    options,
                    "person_last_name",
                    "DJANGO_SUPERUSER_PERSON_LAST_NAME",
                )

            user_data["person_primary_email"] = user_data[self.UserModel.USERNAME_FIELD]
            self.UserModel._default_manager.db_manager(database).create_superuser(
                **user_data
            )
            if options["verbosity"] >= 1:
                self.stdout.write("Superuser created successfully.")
        except KeyboardInterrupt:
            self.stderr.write("\nOperation cancelled.")
            sys.exit(1)
        except exceptions.ValidationError as error:
            raise CommandError("; ".join(error.messages))

    def _prompt_non_model_field(self, value, message):
        while not value:
            value = input(message).strip()
            if not value:
                self.stderr.write("Error: This field cannot be blank.")
        return value

    def _get_noninteractive_value(self, options, option_name, env_var):
        value = options.get(option_name) or os.environ.get(env_var)
        if not value:
            raise CommandError(
                "You must use --%s with --noinput."
                % option_name.replace("_", "-")
            )
        return value
