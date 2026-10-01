import pytest

from calsync.classify import classify, summarize
from calsync.config import ConfigError, parse_config
from calsync.portal import parse_lesson

from .conftest import raw_session


def category_of(config, **kwargs):
    return classify(parse_lesson(raw_session(1, **kwargs)), config).name


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"code": "CM", "unit": "Mécanique du vol"}, "classes"),
        ({"code": "TD", "unit": "Mathématiques"}, "classes"),
        ({"code": "BE", "unit": "Aérodynamique"}, "be"),
        ({"code": "BEN", "unit": "Aérodynamique"}, "critical"),
        ({"code": "BE", "unit": "Structures", "description": "BE noté"}, "critical"),
        ({"code": "BE", "unit": "Structures (BE notée)"}, "critical"),
        ({"code": "EX", "unit": "Thermodynamique"}, "critical"),
        # real title from the portal: "ex" inside "ex-2A" must not mean "exam"
        ({"code": "PRE", "unit": "Fondation (pour les ex-2A) + Alumnis + OSE"}, "classes"),
        ({"code": "CM", "unit": "Contrôle (ex)"}, "classes"),
        ({"code": "ORAUX", "unit": "Projet Ingénierie et Entreprise"}, "critical"),
        ({"code": "ORAUX", "unit": "Forum des langues"}, "critical"),
        ({"code": "BE", "unit": "Séminaires"}, "be"),
        ({"code": "CM", "unit": "Examen de Physique"}, "critical"),
        ({"code": "CM", "unit": "Mécanique", "is_exam": True}, "critical"),
        ({"code": None, "unit": None, "description": "Partiel de maths"}, "critical"),
    ],
)
def test_default_rules(config, kwargs, expected):
    assert category_of(config, **kwargs) == expected


def test_no_false_positive_on_words_containing_be_or_ex(config):
    # 'BE' and 'EX' must match whole words only.
    assert category_of(config, code="CM", unit="Benchmarking des exoplanètes") == "classes"
    assert category_of(config, code="CM", unit="Bernoulli et expérimentation") == "classes"


def test_critical_wins_over_plain_be(config):
    assert category_of(config, code="BE", unit="Aéro", description="BEN") == "critical"


def test_summarize_counts_per_title(config):
    lessons = [
        parse_lesson(raw_session(1, code="BE", unit="Aéro")),
        parse_lesson(raw_session(2, code="BE", unit="Aéro")),
        parse_lesson(raw_session(3, code="CM", unit="Maths")),
    ]
    from calsync.classify import classify_all

    summary = summarize(lessons, classify_all(lessons, config), config)
    assert summary["be"] == {"BE · Aéro": 2}
    assert summary["classes"] == {"CM · Maths": 1}
    assert summary["critical"] == {}


def test_config_validation():
    with pytest.raises(ConfigError, match="missing \\[default\\]"):
        parse_config("[portal]\nurl='x'")
    with pytest.raises(ConfigError, match="regular expression"):
        parse_config('[default]\nname="d"\ncalendar="c"\ncolor_id="1"\n'
                     '[[categories]]\nname="a"\ncalendar="a"\ncolor_id="2"\nrules=["("]')
    with pytest.raises(ConfigError, match="own calendar"):
        parse_config('[default]\nname="d"\ncalendar="same"\ncolor_id="1"\n'
                     '[[categories]]\nname="a"\ncalendar="same"\ncolor_id="2"')


@pytest.mark.parametrize(
    ("kwargs", "ignored"),
    [
        ({"code": "REU", "unit": None, "description": "VACANCES DE NOEL"}, True),
        ({"code": "REU", "unit": None, "description": "FERIE"}, True),
        ({"code": "REU", "unit": None, "description": "Festival futurs proches (pas de cours)"}, True),
        ({"code": "PRE", "unit": "Présentation FILIERES :"}, True),
        ({"code": "PRE", "unit": "Assistante sociale"}, True),
        ({"code": "REU", "unit": "Séminaires"}, False),
        ({"code": "REU", "unit": None, "description": "Village entreprises"}, False),
        ({"code": "CM", "unit": "Machine Learning"}, False),
        ({"code": "BEN", "unit": "Machine Learning"}, False),
        # a title that merely contains the letters 'pre' must not be dropped
        ({"code": "CM", "unit": "Prédiction et estimation"}, False),
    ],
)
def test_ignore_rules(config, kwargs, ignored):
    from calsync.classify import is_ignored

    assert is_ignored(parse_lesson(raw_session(1, **kwargs)), config) is ignored


def test_filter_ignored_splits_lessons(config):
    from calsync.classify import filter_ignored

    lessons = [
        parse_lesson(raw_session(1, code="CM", unit="Maths")),
        parse_lesson(raw_session(2, code="PRE", unit="Accueil")),
    ]
    kept, dropped = filter_ignored(lessons, config)
    assert [lesson.id for lesson in kept] == ["1"] and [lesson.id for lesson in dropped] == ["2"]


def test_invalid_ignore_rule_is_reported():
    with pytest.raises(ConfigError, match="ignore"):
        parse_config('[default]\nname="d"\ncalendar="c"\ncolor_id="1"\n[ignore]\nrules=["("]')
