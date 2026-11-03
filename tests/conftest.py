import pytest


@pytest.fixture(autouse=True)
def no_network_focus(monkeypatch):
    """Os ciclos completos (app.run) não devem buscar o Focus na rede; testes do macro usam o módulo direto."""
    from imoveis import __main__ as app

    class Stub:
        @staticmethod
        def update(*a, **k):
            return None

    monkeypatch.setattr(app, "macro", Stub)
