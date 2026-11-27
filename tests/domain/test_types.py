from arete.domain.models import deck_parents


def test_deck_parents_simple():
    assert deck_parents("Math::Algebra") == ["Math"]


def test_deck_parents_deep():
    assert deck_parents("A::B::C::D") == ["A", "A::B", "A::B::C"]


def test_deck_parents_root():
    assert deck_parents("Math") == []


def test_deck_parents_complex():
    parents = deck_parents("Space::Oddity::Major::Tom")
    assert "Space" in parents
    assert "Space::Oddity" in parents
