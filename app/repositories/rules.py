from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Rule

MAX_RULES_PER_USER = 200


def list_for_user(db: Session, user_id: int) -> list[Rule]:
    return list(db.scalars(select(Rule).where(Rule.user_id == user_id).order_by(Rule.id)))


def count_for_user(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Rule).where(Rule.user_id == user_id)) or 0


def get_for_user(db: Session, user_id: int, rule_id: int) -> Rule | None:
    return db.scalar(select(Rule).where(Rule.id == rule_id, Rule.user_id == user_id))


def find(db: Session, user_id: int, kind: str, pattern: str) -> Rule | None:
    return db.scalar(select(Rule).where(Rule.user_id == user_id, Rule.kind == kind, Rule.pattern == pattern))


def create(db: Session, user_id: int, kind: str, pattern: str, category: str) -> Rule:
    rule = Rule(user_id=user_id, kind=kind, pattern=pattern, category=category)
    db.add(rule)
    db.commit()
    return rule


def upsert(db: Session, user_id: int, kind: str, pattern: str, category: str) -> Rule:
    """Creates the rule, or changes the category of the identical existing one."""
    rule = find(db, user_id, kind, pattern)
    if rule is None:
        return create(db, user_id, kind, pattern, category)
    rule.category = category
    db.commit()
    return rule


def delete(db: Session, rule: Rule) -> None:
    db.delete(rule)
    db.commit()
