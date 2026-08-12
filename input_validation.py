import re
import unicodedata


EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 72
COMMON_PASSWORDS = {
    "123456789012",
    "password1234",
    "qwerty123456",
    "test12345678",
}


def contains_control_characters(value):
    return any(unicodedata.category(character).startswith("C") for character in value)


def is_valid_email(email):
    if not email or len(email) > 120 or contains_control_characters(email):
        return False
    if not EMAIL_PATTERN.fullmatch(email):
        return False
    local_part, domain = email.rsplit("@", 1)
    return len(local_part) <= 64 and len(domain) <= 253


def validate_display_text(value, max_length, field_label):
    if not value:
        return f"{field_label}을(를) 입력해 주세요."
    if len(value) > max_length:
        return f"{field_label}은(는) {max_length}자 이하여야 합니다."
    if contains_control_characters(value):
        return f"{field_label}에 사용할 수 없는 문자가 포함되어 있습니다."
    return None


def password_policy_error(password, label="비밀번호"):
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"{label}는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"{label}는 {MAX_PASSWORD_LENGTH}자 이하여야 합니다."
    if password.casefold() in COMMON_PASSWORDS:
        return "추측하기 쉬운 비밀번호는 사용할 수 없습니다."
    return None
