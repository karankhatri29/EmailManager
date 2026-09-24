from email.utils import parseaddr


def sender_address(sender: str) -> str:
    """The bare, lower-cased address of a "Name <addr@host>" sender ('' if there is none)."""
    address = parseaddr(sender or "")[1].strip().lower()
    return address if "@" in address else ""


def sender_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1] if "@" in address else ""
