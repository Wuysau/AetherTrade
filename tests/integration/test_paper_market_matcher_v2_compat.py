"""Run the unchanged full V1 behavioral cohort against the actual V2 SQL kernel."""
from integration.test_paper_market_matcher import *
from integration import test_paper_market_matcher as legacy


@pytest.fixture(scope='module')
def venue(database):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from trading_system.engine.paper_market_v2 import provision_paper_market_v2
    roles=legacy.venue.__wrapped__(database)
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(roles.runtime_dsn)['dbname']})
    provision_paper_market_v2(admin,roles)
    return roles
