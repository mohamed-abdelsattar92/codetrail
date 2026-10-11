"""Documentation metrics: how well a repository explains itself, from the guide, the facts and the history (design
section 18).

Each metric is a pure function over data already read; `report.build_report` reads it, and `history` keeps each
metric's value per update. Nothing here calls an assistant.
"""
