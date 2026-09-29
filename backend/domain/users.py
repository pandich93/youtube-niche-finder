"""Who owns personal data (plan 15 rule 3): until multi-user mode lands, every
personal row belongs to the one local user. Personal tables carry
`user_id BIGINT NOT NULL DEFAULT 1` and application functions take
`user_id: int = LOCAL_USER_ID` -- there is no global "current user" state.
"""
LOCAL_USER_ID = 1
