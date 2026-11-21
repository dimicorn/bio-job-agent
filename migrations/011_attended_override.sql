-- A person's "apply anyway" from review: lets attended modes pass the score gate, never auto mode.
ALTER TABLE score_overrides ADD COLUMN attended INTEGER NOT NULL DEFAULT 0;
