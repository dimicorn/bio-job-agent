ALTER TABLE application_approvals ADD COLUMN acknowledged_warnings TEXT NOT NULL DEFAULT '{}';
