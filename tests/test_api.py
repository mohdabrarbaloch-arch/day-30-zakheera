"""End-to-end API tests: auth, workspaces, documents, query, admin.

These exercise the real HTTP surface, including the status codes the README
promises. The abstention test is the most important one here: it asserts that
a question the corpus cannot answer returns 200 with ``answered: false``
rather than a confident wrong answer.
"""

from __future__ import annotations

import io

POLICY = """# Refund Policy

Customers may request a refund within thirty days of delivery. The refund is
issued to the original payment method within five working days.

Refunds above 50000 PKR require approval from the finance director before they
are processed. Partial refunds are permitted where only part of an order is
returned.

Shipping costs are not refunded unless the return is due to a defect.
"""


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestHealth:
    def test_health_endpoint(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_index_page_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Zakheera" in response.text


class TestAuth:
    def test_register_returns_token_and_user(self, client):
        response = client.post(
            "/api/auth/register",
            json={"email": "a@example.com", "password": "a-good-password", "display_name": "A"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["access_token"]
        assert body["user"]["email"] == "a@example.com"
        assert "password" not in body["user"]

    def test_first_user_is_admin(self, client):
        response = client.post(
            "/api/auth/register",
            json={"email": "first@example.com", "password": "a-good-password"},
        )
        assert response.json()["user"]["is_admin"] is True

    def test_second_user_is_not_admin(self, client):
        client.post(
            "/api/auth/register",
            json={"email": "first@example.com", "password": "a-good-password"},
        )
        response = client.post(
            "/api/auth/register",
            json={"email": "second@example.com", "password": "a-good-password"},
        )
        assert response.json()["user"]["is_admin"] is False

    def test_duplicate_email_is_409(self, client):
        payload = {"email": "dup@example.com", "password": "a-good-password"}
        assert client.post("/api/auth/register", json=payload).status_code == 201
        assert client.post("/api/auth/register", json=payload).status_code == 409

    def test_short_password_is_422(self, client):
        response = client.post(
            "/api/auth/register", json={"email": "x@example.com", "password": "short"}
        )
        assert response.status_code == 422

    def test_invalid_email_is_422(self, client):
        response = client.post(
            "/api/auth/register", json={"email": "not-an-email", "password": "a-good-password"}
        )
        assert response.status_code == 422

    def test_login_succeeds_with_correct_password(self, client):
        client.post(
            "/api/auth/register",
            json={"email": "login@example.com", "password": "a-good-password"},
        )
        response = client.post(
            "/api/auth/login", json={"email": "login@example.com", "password": "a-good-password"}
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_login_wrong_password_is_401(self, client):
        client.post(
            "/api/auth/register",
            json={"email": "login@example.com", "password": "a-good-password"},
        )
        response = client.post(
            "/api/auth/login", json={"email": "login@example.com", "password": "wrong-password"}
        )
        assert response.status_code == 401

    def test_login_unknown_email_is_401_not_404(self, client):
        """A 404 would confirm the account does not exist, which is an
        account-enumeration oracle."""
        response = client.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": "a-good-password"}
        )
        assert response.status_code == 401

    def test_me_requires_a_token(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_me_rejects_a_garbage_token(self, client):
        assert client.get("/api/auth/me", headers=_auth("not.a.token")).status_code == 401

    def test_me_returns_the_current_user(self, auth_client):
        client, token, user = auth_client
        response = client.get("/api/auth/me", headers=_auth(token))
        assert response.status_code == 200
        assert response.json()["email"] == user["email"]


class TestWorkspaces:
    def test_create_and_list(self, auth_client):
        client, token, _ = auth_client
        created = client.post("/api/workspaces", json={"name": "My Base"}, headers=_auth(token))
        assert created.status_code == 201
        assert created.json()["slug"] == "my-base"

        listed = client.get("/api/workspaces", headers=_auth(token))
        assert listed.status_code == 200
        assert len(listed.json()) == 1

    def test_duplicate_names_get_unique_slugs(self, auth_client):
        client, token, _ = auth_client
        first = client.post("/api/workspaces", json={"name": "Handbook"}, headers=_auth(token))
        second = client.post("/api/workspaces", json={"name": "Handbook"}, headers=_auth(token))
        assert first.json()["slug"] == "handbook"
        assert second.json()["slug"] == "handbook-2"

    def test_requires_authentication(self, client):
        assert client.get("/api/workspaces").status_code == 401

    def test_unknown_workspace_is_404(self, auth_client):
        client, token, _ = auth_client
        assert client.get("/api/workspaces/9999", headers=_auth(token)).status_code == 404

    def test_another_users_workspace_is_404_not_403(self, client):
        """403 would confirm the workspace exists."""
        owner = client.post(
            "/api/auth/register",
            json={"email": "owner@example.com", "password": "a-good-password"},
        ).json()
        intruder = client.post(
            "/api/auth/register",
            json={"email": "intruder@example.com", "password": "a-good-password"},
        ).json()

        workspace = client.post(
            "/api/workspaces",
            json={"name": "Private"},
            headers=_auth(owner["access_token"]),
        ).json()

        response = client.get(
            f"/api/workspaces/{workspace['id']}", headers=_auth(intruder["access_token"])
        )
        assert response.status_code == 404

    def test_delete_workspace(self, auth_client):
        client, token, _ = auth_client
        workspace = client.post(
            "/api/workspaces", json={"name": "Temp"}, headers=_auth(token)
        ).json()
        assert (
            client.delete(f"/api/workspaces/{workspace['id']}", headers=_auth(token)).status_code
            == 204
        )
        assert (
            client.get(f"/api/workspaces/{workspace['id']}", headers=_auth(token)).status_code
            == 404
        )

    def test_stats_start_at_zero(self, workspace):
        client, token, ws = workspace
        response = client.get(f"/api/workspaces/{ws['id']}/stats", headers=_auth(token))
        assert response.status_code == 200
        body = response.json()
        assert body["document_count"] == 0
        assert body["question_count"] == 0
        assert body["answer_rate"] == 0.0


class TestDocuments:
    def test_ingest_text_creates_chunks(self, workspace):
        client, token, ws = workspace
        response = client.post(
            f"/api/workspaces/{ws['id']}/documents/text",
            json={"title": "Refund Policy", "content": POLICY},
            headers=_auth(token),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["chunk_count"] >= 1
        assert body["char_count"] == len(POLICY)

    def test_upload_txt_file(self, workspace):
        client, token, ws = workspace
        response = client.post(
            f"/api/workspaces/{ws['id']}/documents/upload",
            files={"file": ("policy.txt", io.BytesIO(POLICY.encode()), "text/plain")},
            headers=_auth(token),
        )
        assert response.status_code == 201
        assert response.json()["filename"] == "policy.txt"

    def test_upload_unsupported_type_is_415(self, workspace):
        client, token, ws = workspace
        response = client.post(
            f"/api/workspaces/{ws['id']}/documents/upload",
            files={"file": ("image.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
            headers=_auth(token),
        )
        assert response.status_code == 415

    def test_upload_empty_file_is_422(self, workspace):
        client, token, ws = workspace
        response = client.post(
            f"/api/workspaces/{ws['id']}/documents/upload",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
            headers=_auth(token),
        )
        assert response.status_code == 422

    def test_list_and_preview_document(self, workspace):
        client, token, ws = workspace
        created = client.post(
            f"/api/workspaces/{ws['id']}/documents/text",
            json={"title": "Refund Policy", "content": POLICY},
            headers=_auth(token),
        ).json()

        listed = client.get(f"/api/workspaces/{ws['id']}/documents", headers=_auth(token))
        assert len(listed.json()) == 1

        detail = client.get(
            f"/api/workspaces/{ws['id']}/documents/{created['id']}", headers=_auth(token)
        )
        assert detail.status_code == 200
        assert "Refund Policy" in detail.json()["preview"]

    def test_delete_document(self, workspace):
        client, token, ws = workspace
        created = client.post(
            f"/api/workspaces/{ws['id']}/documents/text",
            json={"title": "Temp", "content": POLICY},
            headers=_auth(token),
        ).json()
        response = client.delete(
            f"/api/workspaces/{ws['id']}/documents/{created['id']}", headers=_auth(token)
        )
        assert response.status_code == 204
        assert (
            client.get(f"/api/workspaces/{ws['id']}/documents", headers=_auth(token)).json() == []
        )

    def test_unknown_document_is_404(self, workspace):
        client, token, ws = workspace
        assert (
            client.get(
                f"/api/workspaces/{ws['id']}/documents/9999", headers=_auth(token)
            ).status_code
            == 404
        )


class TestQuery:
    def _seed(self, client, token, ws_id):
        client.post(
            f"/api/workspaces/{ws_id}/documents/text",
            json={"title": "Refund Policy", "content": POLICY},
            headers=_auth(token),
        )

    def test_answers_a_question_the_corpus_covers(self, workspace):
        client, token, ws = workspace
        self._seed(client, token, ws["id"])

        response = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "How long do customers have to request a refund?"},
            headers=_auth(token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["answered"] is True
        assert body["citations"]
        assert "thirty days" in body["answer"]

    def test_citation_offsets_point_at_the_quote(self, workspace):
        """The end-to-end version of the core guarantee."""
        client, token, ws = workspace
        self._seed(client, token, ws["id"])

        body = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "How long do customers have to request a refund?"},
            headers=_auth(token),
        ).json()

        for citation in body["citations"]:
            assert POLICY[citation["start_char"] : citation["end_char"]] == citation["quote"]

    def test_abstains_on_an_uncovered_question(self, workspace):
        """The most important behaviour: 200 with answered=false, not a
        confident wrong answer."""
        client, token, ws = workspace
        self._seed(client, token, ws["id"])

        response = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "What is the company policy on cryptocurrency trading?"},
            headers=_auth(token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["answered"] is False
        assert body["answer"] == ""
        assert body["abstain_reason"]
        assert body["coverage"] < body["coverage_threshold"]
        assert "cryptocurrency" in body["missing_terms"]

    def test_abstains_on_an_empty_workspace(self, workspace):
        client, token, ws = workspace
        body = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "Anything at all?"},
            headers=_auth(token),
        ).json()
        assert body["answered"] is False
        assert "no documents" in body["abstain_reason"].lower()

    def test_returns_retrieved_passages_with_both_scores(self, workspace):
        client, token, ws = workspace
        self._seed(client, token, ws["id"])
        body = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "refund policy"},
            headers=_auth(token),
        ).json()
        assert body["retrieved"]
        first = body["retrieved"][0]
        assert "bm25_score" in first
        assert "vector_score" in first
        assert "fused_score" in first

    def test_question_too_short_is_422(self, workspace):
        client, token, ws = workspace
        response = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "a"},
            headers=_auth(token),
        )
        assert response.status_code == 422

    def test_top_k_out_of_range_is_422(self, workspace):
        client, token, ws = workspace
        response = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "a real question", "top_k": 999},
            headers=_auth(token),
        )
        assert response.status_code == 422

    def test_query_requires_authentication(self, workspace):
        client, _, ws = workspace
        response = client.post(
            f"/api/workspaces/{ws['id']}/query", json={"question": "a real question"}
        )
        assert response.status_code == 401

    def test_questions_are_logged(self, workspace):
        client, token, ws = workspace
        self._seed(client, token, ws["id"])
        client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "How long do customers have to request a refund?"},
            headers=_auth(token),
        )
        logs = client.get(f"/api/workspaces/{ws['id']}/questions", headers=_auth(token)).json()
        assert len(logs) == 1
        assert logs[0]["answered"] is True

    def test_lexical_gap_causes_an_honest_abstention(self, workspace):
        """A real limitation, pinned so it cannot regress silently.

        "How long is the refund window?" is a fair question about this
        policy, but the corpus says "within thirty days of delivery" — it
        never uses the word "window". A lexical retriever cannot know that
        "window" and "period" mean the same thing, so it abstains rather
        than guessing. That is the correct trade-off for a tool whose whole
        promise is that it does not make things up, but it is a limitation
        worth knowing about: rephrasing to the corpus's own vocabulary gets
        an answer.
        """
        client, token, ws = workspace
        self._seed(client, token, ws["id"])

        vague = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "How long is the refund window?"},
            headers=_auth(token),
        ).json()
        assert vague["answered"] is False
        assert "window" in vague["missing_terms"]

        precise = client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "How long do customers have to request a refund?"},
            headers=_auth(token),
        ).json()
        assert precise["answered"] is True

    def test_stats_reflect_questions(self, workspace):
        client, token, ws = workspace
        self._seed(client, token, ws["id"])
        client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "How long do customers have to request a refund?"},
            headers=_auth(token),
        )
        client.post(
            f"/api/workspaces/{ws['id']}/query",
            json={"question": "What about cryptocurrency trading?"},
            headers=_auth(token),
        )
        stats = client.get(f"/api/workspaces/{ws['id']}/stats", headers=_auth(token)).json()
        assert stats["question_count"] == 2
        assert stats["answered_count"] == 1
        assert stats["abstained_count"] == 1
        assert stats["answer_rate"] == 0.5


class TestAdmin:
    def test_admin_stats_for_the_first_user(self, auth_client):
        client, token, _ = auth_client
        response = client.get("/api/admin/stats", headers=_auth(token))
        assert response.status_code == 200
        body = response.json()
        assert body["user_count"] == 1
        assert body["workspace_count"] == 0

    def test_non_admin_is_403(self, client):
        client.post(
            "/api/auth/register",
            json={"email": "admin@example.com", "password": "a-good-password"},
        )
        second = client.post(
            "/api/auth/register",
            json={"email": "pleb@example.com", "password": "a-good-password"},
        ).json()
        response = client.get("/api/admin/stats", headers=_auth(second["access_token"]))
        assert response.status_code == 403

    def test_admin_requires_authentication(self, client):
        assert client.get("/api/admin/stats").status_code == 401


class TestSecurityHeaders:
    def test_security_headers_are_present(self, client):
        response = client.get("/api/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"

    def test_validation_errors_do_not_echo_the_password(self, client):
        """FastAPI's default error body includes the raw input, which would
        put the submitted password in the response."""
        response = client.post(
            "/api/auth/register", json={"email": "bad", "password": "super-secret-value"}
        )
        assert response.status_code == 422
        assert "super-secret-value" not in response.text
