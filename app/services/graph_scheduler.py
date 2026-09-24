import networkx as nx

from .nlp_engine import (
    extract_action_task,
    extract_explicit_deadline,
    extract_project_context,
    nlp,
    process_text,
)


def build_scheduler_graph(emails_list):
    """
    Constructs an in-memory NetworkX relationship tree and runs tier sorting math
    to output tasks in strict user-defined priority order.
    """
    if not emails_list:
        return []

    G = nx.DiGraph()
    project_task_counts: dict[str, int] = {}

    # Pass 1: Count tasks per project to identify multi-item cluster streams (Subtasks)
    for email in emails_list:
        if email.get("category") in ["Promotional", "General"]:
            continue
        project = extract_project_context(email["subject"], email["body"].lower())
        project_task_counts[project] = project_task_counts.get(project, 0) + 1

    # Pass 2: Assemble Graph infrastructure mapping nodes and extraction entities
    for email in emails_list:
        if email.get("category") in ["Promotional", "General"]:
            continue

        msg_id = email["id"]
        sender = email["sender"].split("<")[0].strip()
        subject = email["subject"]
        body = email["body"]

        project = extract_project_context(subject, body.lower())

        # NLP Token Mining layers
        nlp_data = process_text(f"{subject} {body}")
        doc_raw = nlp(f"{subject} {body}")

        task_str = extract_action_task(subject, doc_raw, nlp_data["clean_text_raw"])
        deadline_meta = extract_explicit_deadline(nlp_data["clean_text_raw"], nlp_data["entities"])

        # Determine strict programmatic sorting hierarchy rank rows
        has_subtasks = project_task_counts.get(project, 1) > 1
        tier = deadline_meta["tier"]

        # Adjust rank splits dynamically for Tier 1 vs Tier 2 based on subtask cluster counts
        if tier == 1 and not has_subtasks:
            tier = 2  # Drops down to Tier 2 priority rank if it is isolated (no subtasks)

        # Build Graph Data Map Nodes
        G.add_node(
            msg_id,
            type="Task",
            sender=sender,
            task=task_str,
            deadline=deadline_meta["value"],
            sort_tier=tier,  # Lower tiers rise to the top (1 = highest)
            base_score=email.get("score", 2.0),
        )

        G.add_edge(sender, msg_id)
        G.add_edge(msg_id, project)

    # Pass 3: Package datasets directly into a structural ordered list
    scheduled_tasks = []
    for node, attr in G.nodes(data=True):
        if attr.get("type") == "Task":
            scheduled_tasks.append(
                {
                    "id": node,
                    "sender": attr["sender"],
                    "task": attr["task"],
                    "deadline": attr["deadline"],
                    "sort_tier": attr["sort_tier"],
                    "base_score": attr["base_score"],
                }
            )

    # Sort strictly matching user constraints: Sort Tier first (1 -> 4), then break ties using the NLP weight score
    scheduled_tasks.sort(key=lambda x: (x["sort_tier"], -x["base_score"]))
    return scheduled_tasks
