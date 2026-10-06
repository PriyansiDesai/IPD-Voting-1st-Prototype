CREATE OR REPLACE FUNCTION check_session_choices_freeze()
RETURNS TRIGGER AS $$
DECLARE
    session_status TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT status INTO session_status FROM voting_sessions WHERE session_id = OLD.session_id FOR SHARE;
        IF session_status != 'DRAFT' THEN
            RAISE EXCEPTION 'Cannot modify choices for a session that is not in DRAFT status';
        END IF;
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        SELECT status INTO session_status FROM voting_sessions WHERE session_id = OLD.session_id FOR SHARE;
        IF session_status != 'DRAFT' THEN
            RAISE EXCEPTION 'Cannot modify choices for a session that is not in DRAFT status';
        END IF;
        
        IF NEW.session_id != OLD.session_id THEN
            SELECT status INTO session_status FROM voting_sessions WHERE session_id = NEW.session_id FOR SHARE;
            IF session_status != 'DRAFT' THEN
                RAISE EXCEPTION 'Cannot modify choices for a session that is not in DRAFT status';
            END IF;
        END IF;
        
        RETURN NEW;
    ELSIF TG_OP = 'INSERT' THEN
        SELECT status INTO session_status FROM voting_sessions WHERE session_id = NEW.session_id FOR SHARE;
        IF session_status != 'DRAFT' THEN
            RAISE EXCEPTION 'Cannot modify choices for a session that is not in DRAFT status';
        END IF;
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER enforce_session_choices_freeze
BEFORE INSERT OR UPDATE OR DELETE ON session_choices
FOR EACH ROW EXECUTE FUNCTION check_session_choices_freeze();
