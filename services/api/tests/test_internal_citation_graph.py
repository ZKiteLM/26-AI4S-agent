from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.api.app.citation import citation_graph_service as graph_service
from services.api.app.citation import internal_citation_graph as internal
from services.api.app.citation.citation_graph_service import add_seed_paper, serialize_view
from services.api.app.citation.citation_providers import OpenAlexProvider, _matching
from services.api.app.db import Base
from services.api.app.main import GraphIn
from services.api.app.models import (
    Block,
    GraphView,
    Paper,
    ScholarlyCitationEdge,
    ScholarlyWork,
    Version,
)


def database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_graph_request_accepts_more_than_twenty_library_papers():
    data = GraphIn.model_validate({"mode": "internal", "paper_ids": [str(i) for i in range(35)]})
    assert len(data.paper_ids) == 35


def test_identity_matching_accepts_arxiv_preprint_and_proceedings_dois():
    assert _matching(
        {"title": "QLoRA", "arxiv_id": "2305.14314", "doi": "10.52202/example"},
        {"title": "QLoRA", "arxiv_id": "2305.14314", "doi": "10.48550/arxiv.2305.14314"},
    )


def test_existing_s2_id_does_not_prevent_openalex_enrichment(monkeypatch):
    monkeypatch.setattr(
        graph_service,
        "resolve_paper",
        lambda _: (
            {
                "title": "A Paper",
                "arxiv_id": "2305.14314",
                "doi": "10.52202/example",
                "openalex_id": "W123",
            },
            "openalex",
        ),
    )
    with database() as db:
        work = ScholarlyWork(
            title="A Paper",
            arxiv_id="2305.14314",
            doi="10.48550/arxiv.2305.14314",
            s2_paper_id="a" * 40,
        )
        db.add(work)
        db.commit()
        assert graph_service.attach_identity(db, work)
        assert work.openalex_id == "W123"


def test_exact_title_can_resolve_without_local_author_metadata(monkeypatch):
    provider = OpenAlexProvider()
    monkeypatch.setattr(
        provider,
        "_get",
        lambda *args, **kwargs: {
            "results": [
                {
                    "display_name": "LLaMA: Open and Efficient Foundation Language Models",
                    "id": "https://openalex.org/W123",
                    "authorships": [],
                    "locations": [],
                }
            ]
        },
    )
    result = provider.resolve_paper(
        {"title": "LLaMA: Open and Efficient Foundation Language Models"}
    )
    assert result["openalex_id"] == "https://openalex.org/W123"


def test_parsed_pdf_references_support_papers_without_external_ids(monkeypatch):
    monkeypatch.setattr(internal, "attach_identity", lambda *args: False)
    with database() as db:
        view = GraphView(owner_id="owner", config={"mode": "internal"})
        db.add(view)
        db.flush()
        papers = [
            Paper(owner_id="owner", title="Improving Language Understanding"),
            Paper(owner_id="owner", title="Another Research Paper"),
        ]
        db.add_all(papers)
        db.flush()
        source = add_seed_paper(db, view, papers[1])
        target = add_seed_paper(db, view, papers[0])
        for paper, text in zip(
            papers,
            [
                "Improving language understanding by generative pre training Alec Radford OpenAI",
                "Another research paper by Example Author",
            ],
            strict=True,
        ):
            version = Version(
                paper_id=paper.id,
                filename="paper.pdf",
                file_key="x",
                sha256=paper.id,
                parse_status="completed",
                sample_text=text,
            )
            db.add(version)
            db.flush()
            if paper.id == papers[1].id:
                db.add_all(
                    [
                        Block(version_id=version.id, page_index=0, order=0, text="References"),
                        Block(
                            version_id=version.id,
                            page_index=0,
                            order=1,
                            text="Alec Radford. Improving language understanding by generative pre-training.",
                        ),
                    ]
                )
            else:
                db.add(Block(version_id=version.id, page_index=0, order=0, text="References"))
        db.commit()
        assert internal.build_internal_graph(db, view) == []
        graph = serialize_view(db, view)
        assert graph["metrics"]["verified_papers"] == 2
        assert graph["edges"][0]["source"] == source.id
        assert graph["edges"][0]["target"] == target.id


def test_internal_graph_only_connects_selected_papers_and_ranks_within_set(monkeypatch):
    class OpenAlex:
        name = "openalex"
        calls = 0

        def get_reference_ids(self, identifier):
            self.calls += 1
            return {"W1": ["W2", "W999"], "W2": ["W999"], "W3": []}[identifier]

    provider = OpenAlex()
    monkeypatch.setattr(internal, "OpenAlexProvider", lambda: provider)
    with database() as db:
        view = GraphView(owner_id="owner", config={"mode": "internal"})
        db.add(view)
        db.flush()
        for index in range(1, 4):
            paper = Paper(owner_id="owner", title=f"Paper {index}", openalex_id=f"W{index}")
            db.add(paper)
            db.flush()
            add_seed_paper(db, view, paper)
        db.commit()
        assert internal.build_internal_graph(db, view) == []
        graph = serialize_view(db, view)
        assert len(graph["nodes"]) == 3
        assert len(graph["edges"]) == 1
        by_title = {node["title"]: node for node in graph["nodes"]}
        assert graph["edges"][0]["source"] == by_title["Paper 1"]["id"]
        assert graph["edges"][0]["target"] == by_title["Paper 2"]["id"]
        assert by_title["Paper 2"]["role"] == "foundation"
        assert by_title["Paper 1"]["role"] == "derivative"
        assert by_title["Paper 3"]["role"] == "isolated"
        assert graph["metrics"]["verified_papers"] == 3
        assert len(db.scalars(select(Paper)).all()) == 3
        assert len(db.scalars(select(ScholarlyWork)).all()) == 3
        assert len(db.scalars(select(ScholarlyCitationEdge)).all()) == 1
        assert internal.build_internal_graph(db, view) == []
        assert provider.calls == 3
