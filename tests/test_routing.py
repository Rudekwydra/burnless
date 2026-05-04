from burnless import routing


def test_absolute_path_routes_to_silver_without_config_keyword():
    tier, matched = routing.route(
        "olha o projeto /Users/roberto/antigravity/app_paty",
        {"gold": [], "silver": [], "bronze": []},
    )

    assert tier == "silver"
    assert matched == "path"


def test_project_memory_review_routes_to_silver():
    tier, matched = routing.route(
        "veja se tudo foi feito conforme anotacoes da memoria",
        {"gold": [], "silver": [], "bronze": []},
    )

    assert tier == "silver"
    assert matched == "memoria"


def test_repository_lookup_routes_to_silver():
    tier, matched = routing.route(
        "encontra o repositorio no disco",
        {"gold": [], "silver": [], "bronze": []},
    )

    assert tier == "silver"
    assert matched == "repositorio"


def test_explicit_gold_keyword_wins_over_path_hint():
    tier, matched = routing.route(
        "faça uma revisão de arquitetura em /Users/roberto/app",
        {"gold": ["arquitetura"], "silver": [], "bronze": []},
    )

    assert tier == "gold"
    assert matched == "arquitetura"
