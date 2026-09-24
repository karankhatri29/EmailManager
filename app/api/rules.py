from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.models import User
from ..db.session import get_db
from ..repositories import rules as rules_repo
from ..schemas import RuleCreated, RuleIn, RuleOut
from ..services import email_actions
from ..services.rules import RuleError, normalize
from .deps import current_user

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return rules_repo.list_for_user(db, user.id)


@router.post("", response_model=RuleCreated, status_code=201)
def create_rule(payload: RuleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Adds a rule and applies it to the mail already stored (not to mail you corrected by hand)."""
    try:
        spec = normalize(payload.kind, payload.pattern, payload.category)
    except RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    if rules_repo.count_for_user(db, user.id) >= rules_repo.MAX_RULES_PER_USER:
        raise HTTPException(status_code=422, detail=f"You can have at most {rules_repo.MAX_RULES_PER_USER} rules")
    if rules_repo.find(db, user.id, spec.kind, spec.pattern) is not None:
        raise HTTPException(status_code=409, detail="You already have a rule for that")

    try:
        rule = rules_repo.create(db, user.id, spec.kind, spec.pattern, spec.category)
    except IntegrityError:  # two requests raced
        db.rollback()
        raise HTTPException(status_code=409, detail="You already have a rule for that") from None

    affected = email_actions.reapply_rules(db, user.id, spec.kind, spec.pattern)
    return RuleCreated(
        id=rule.id,
        kind=rule.kind,
        pattern=rule.pattern,
        category=rule.category,
        created_at=rule.created_at,
        affected=len(affected),
    )


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Removes a rule; mail it had decided goes back to the automatic classifier."""
    rule = rules_repo.get_for_user(db, user.id, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    kind, pattern = rule.kind, rule.pattern
    rules_repo.delete(db, rule)
    email_actions.reapply_rules(db, user.id, kind, pattern)
    return Response(status_code=204)
