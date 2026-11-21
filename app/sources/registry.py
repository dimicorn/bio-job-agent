from app.sources.ats.ashby import Ashby
from app.sources.ats.greenhouse import Greenhouse
from app.sources.ats.lever import Lever
from app.sources.ats.workable import Workable
from app.sources.boards.jsonfeed import JsonFeed
from app.sources.site import Site

ADAPTERS = {
    "greenhouse": Greenhouse,
    "lever": Lever,
    "ashby": Ashby,
    "workable": Workable,
    "site": Site,
    "jsonfeed": JsonFeed,
}
