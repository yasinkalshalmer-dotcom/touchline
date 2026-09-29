"""Confederation and gender classification for competitions."""
import re

_CONF = {
    "uefa": "ALB AND ARM AUT AZE BLR BEL BIH BUL CRO CYP CZE DEN ENG EST FRO FIN FRA GEO GER GIB GRE HUN ISL ISR ITA "
            "KAZ KOS LVA LIE LTU LUX MLT MDA MNE NED MKD NIR NOR POL POR IRL ROU RUS SMR SCO SRB SVK SVN ESP SWE SUI "
            "TUR UKR WAL DEU NLD PRT CHE HRV GRC DNK BGR",
    "conmebol": "ARG BOL BRA CHI CHL COL ECU PAR PRY PER URU URY VEN",
    "concacaf": "AIA ATG ARU BAH BRB BLZ BER BOE VGB CAN CAY CRC CRI CUB CUW DMA DOM SLV GYF GRN GLP GUA GTM GUY HAI "
                "HTI HON HND JAM MTQ MEX MSR NCA NIC PAN PUR PRI SKN KNA LCA SMN VIN VCT SXM SUR TRI TTO TCA VIR USA",
    "caf": "ALG DZA ANG AGO BEN BOT BWA BFA BDI CPV CMR CTA CAF CHA TCD COM CGO COG COD CIV DJI EGY EQG GNQ ERI SWZ ETH "
           "GAB GAM GMB GHA GUI GIN GNB KEN LES LSO LBR LBY MAD MDG MWI MLI MTN MRT MRI MUS MAR MOZ NAM NIG NER NGA RWA "
           "STP SEN SEY SYC SLE SOM RSA ZAF SSD SUD SDN TAN TZA TOG TGO TUN UGA ZAM ZMB ZIM ZWE REU ZAN",
    "afc": "AFG AUS BHR BAN BGD BHU BTN BRU BRN CAM KHM CHN TPE TWN PRK GUM HKG IND IDN IRN IRQ JPN JOR KOR KUW KWT KGZ "
           "LAO LIB LBN MAC MAS MYS MDV MNG MYA MMR NEP NPL OMA OMN PAK PLE PSE PHI PHL QAT KSA SAU SIN SGP SRI LKA SYR "
           "TJK THA TLS TKM UAE ARE UZB VIE VNM YEM NMI",
    "ofc": "ASA COK FIJ NCL NZL PNG SAM WSM SOL SLB TAH TGA VAN VUT",
}
CC_CONF = {cc: conf for conf, codes in _CONF.items() for cc in codes.split()}

CONF_LABEL = {"uefa": "UEFA", "caf": "CAF", "afc": "AFC", "concacaf": "CONCACAF",
              "conmebol": "CONMEBOL", "ofc": "OFC", "int": "International"}

_WOMEN = re.compile(
    r"Women|Womens|Femenil|Femenin|F[eé]minin|Feminino|Femminile|Frauen|Kvinner|Kvinder|Damallsvenskan|Elitettan|"
    r"Toppserien|Vrouwen|NWSL|\bLiga F\b|\bWSL\b|Ladies|Damen|Naisten|Kansallinen Liiga|Kobiet|Nadeshiko|"
    r"\bWE League\b|\bWK League\b|Northern Super League|Super League W|\(W\)", re.I)


def confederation(ccode: str, name: str) -> str:
    if ccode and ccode != "INT" and ccode in CC_CONF:
        return CC_CONF[ccode]
    n = name or ""
    rules = [
        ("concacaf", r"CONCACAF|Gold Cup|Leagues Cup|Caribbean"),
        ("conmebol", r"CONMEBOL|Copa Am[eé]rica|Libertadores|Sudamericana|Recopa"),
        ("caf", r"\bCAF\b|Africa|AFCON|COSAFA|CECAFA|WAFU"),
        ("afc", r"\bAFC\b|Asia|ASEAN|Gulf Cup|SAFF|WAFF|EAFF|\bAFF\b|Arab"),
        ("ofc", r"\bOFC\b|Oceania|Pacific"),
        ("uefa", r"UEFA|Euro|Nations League|Champions League|Europa|Conference League"),
    ]
    for conf, rx in rules:
        if re.search(rx, n, re.I):
            return conf
    return "int"


def gender(name: str) -> str:
    return "women" if _WOMEN.search(name or "") else "men"
