import unittest

from scripts.verify_sql_syntax import check_sql_text


VALID = """
CREATE FUNCTION public.demo(input text)
RETURNS text
LANGUAGE plpgsql
AS $$
BEGIN
    IF input IS NULL THEN
        RETURN 'fallback';
    END IF;
    RETURN input;
END
$$;
"""


class SqlSyntaxGateTest(unittest.TestCase):
    def test_valid_plpgsql_function_passes(self):
        self.assertEqual(check_sql_text(VALID, source="valid.sql"), 1)

    def test_outer_sql_quote_error_fails(self):
        broken = VALID.replace("'fallback'", "'fallback", 1)
        with self.assertRaisesRegex(ValueError, "valid.sql"):
            check_sql_text(broken, source="valid.sql")

    def test_plpgsql_body_error_fails(self):
        broken = VALID.replace("END IF;", "END WAT;", 1)
        with self.assertRaisesRegex(ValueError, "PL/pgSQL"):
            check_sql_text(broken, source="body.sql")


if __name__ == "__main__":
    unittest.main()
