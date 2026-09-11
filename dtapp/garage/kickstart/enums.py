from enum import Enum


class KickoffStatus(str, Enum):
    # REGISTERING is the earliest lifecycle status: the row exists but the async register-ancillary
    # work (cleanup → Monday → Slack → Linear → token → intake email) hasn't run yet. The register
    # consumer advances it to AWAITING_CUSTOMER_RESPONSE once that work completes.
    REGISTERING = "registering"
    AWAITING_CUSTOMER_RESPONSE = "awaiting_customer_response"
    PROVISIONING = "provisioning"
    AWAITING_CONSULTANT = "awaiting_consultant"
    # CONSULTANT_ASSIGNED is the intermediate status while the async post-assign work (Monday owners +
    # Slack channel-add) runs. assign_consultant sets it then enqueues type:"assign"; the consumer
    # advances it to DONE once that work completes.
    CONSULTANT_ASSIGNED = "consultant_assigned"
    DONE = "done"
    # Set at dead-letter (work permanently gave up) so the kickoff doesn't masquerade as still-in-flight.
    FAILED = "failed"


class ChannelOfCommunication(str, Enum):
    # External customer comms channel — limited to Slack / Teams (Google Chat + WhatsApp dropped).
    SLACK = "Slack"
    MICROSOFT_TEAMS = "Microsoft Teams"


class YesNo(str, Enum):
    # Generic Yes/No — the register form renders it as a dropdown (symmetry with the handoff Yes/No fields).
    YES = "Yes"
    NO = "No"


class WeekStart(str, Enum):
    # Drive parses this via java.time.DayOfWeek.valueOf() (case-sensitive, DateUtil.java) — values
    # MUST be the UPPERCASE DayOfWeek names or Drive's week calculations throw. (Guru's signup used
    # "THURSDAY".) Labels are title-cased on the FE; the stored/sent value is uppercase.
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"


class FiscalYear(str, Enum):
    JAN_DEC = "Jan-Dec"
    FEB_JAN = "Feb-Jan"
    MAR_FEB = "Mar-Feb"
    APR_MAR = "Apr-Mar"
    MAY_APR = "May-Apr"
    JUN_MAY = "Jun-May"
    JUL_JUN = "Jul-Jun"
    AUG_JUL = "Aug-Jul"
    SEP_AUG = "Sep-Aug"
    OCT_SEP = "Oct-Sep"
    NOV_OCT = "Nov-Oct"
    DEC_NOV = "Dec-Nov"


# ── Tenant config: currency + date format (mirror Drive EXACTLY) ──
# Currency: member NAME == code == value. Mirrors Drive's PRODUCT currency list
# (drive-frontend data/currencies) — a strict subset of Drive BE's Currency enum, so
# Currency.valueOf(code) never fails on create. BE-only test/metal/exotic codes
# (XXX, XTS, XAU/XAG/XPT/XPD, XDR, …) are excluded — not customer base currencies.

class Currency(str, Enum):
    USD = "USD"
    INR = "INR"
    EUR = "EUR"
    GBP = "GBP"
    SGD = "SGD"
    AED = "AED"
    AUD = "AUD"
    CAD = "CAD"
    ZAR = "ZAR"
    ETH = "ETH"
    CNY = "CNY"
    JPY = "JPY"
    ILS = "ILS"
    THB = "THB"
    DAI = "DAI"
    DKK = "DKK"
    BGN = "BGN"
    BRL = "BRL"
    IDR = "IDR"
    MXN = "MXN"
    KRW = "KRW"
    RON = "RON"
    CRC = "CRC"
    CLP = "CLP"
    CHF = "CHF"
    EGP = "EGP"
    TND = "TND"
    GHS = "GHS"
    UGX = "UGX"
    NGN = "NGN"
    XOF = "XOF"
    DZD = "DZD"
    MAD = "MAD"
    KES = "KES"
    SEK = "SEK"
    NOK = "NOK"
    PLN = "PLN"
    CZK = "CZK"
    HUF = "HUF"
    RUB = "RUB"
    TRY = "TRY"
    HKD = "HKD"
    TWD = "TWD"
    MYR = "MYR"
    PHP = "PHP"
    VND = "VND"
    PKR = "PKR"
    BDT = "BDT"
    LKR = "LKR"
    TZS = "TZS"
    ZMW = "ZMW"
    MWK = "MWK"
    BWP = "BWP"
    NAD = "NAD"
    SZL = "SZL"
    LSL = "LSL"
    MUR = "MUR"
    SCR = "SCR"
    KMF = "KMF"
    DJF = "DJF"
    ETB = "ETB"
    SDG = "SDG"
    SOS = "SOS"
    ERN = "ERN"
    YER = "YER"
    OMR = "OMR"
    QAR = "QAR"
    KWD = "KWD"
    BHD = "BHD"
    SAR = "SAR"
    JOD = "JOD"
    LBP = "LBP"
    SYP = "SYP"
    IQD = "IQD"
    IRR = "IRR"
    AFN = "AFN"
    NPR = "NPR"
    BTN = "BTN"
    MMK = "MMK"
    LAK = "LAK"
    KHR = "KHR"
    MNT = "MNT"
    KPW = "KPW"
    VUV = "VUV"
    FJD = "FJD"
    WST = "WST"
    TOP = "TOP"
    PGK = "PGK"
    SBD = "SBD"
    NIO = "NIO"
    HNL = "HNL"
    GTQ = "GTQ"
    BZD = "BZD"
    PAB = "PAB"
    TTD = "TTD"
    BBD = "BBD"
    XCD = "XCD"
    ANG = "ANG"
    AWG = "AWG"
    SRD = "SRD"
    GYD = "GYD"
    VES = "VES"
    COP = "COP"
    PEN = "PEN"
    BOB = "BOB"
    ARS = "ARS"
    UYU = "UYU"
    PYG = "PYG"
    UAH = "UAH"
    MDL = "MDL"
    GEL = "GEL"
    AMD = "AMD"
    AZN = "AZN"
    BYN = "BYN"
    KZT = "KZT"
    KGS = "KGS"
    TJS = "TJS"
    TMT = "TMT"
    UZS = "UZS"
    MVR = "MVR"
    BTC = "BTC"
    USDT = "USDT"
    USDC = "USDC"
    BNB = "BNB"
    SOL = "SOL"
    XRP = "XRP"
    ADA = "ADA"
    AVAX = "AVAX"
    DOT = "DOT"
    MATIC = "MATIC"
    XAF = "XAF"
    NZD = "NZD"


# DateFormat: value == the exact Java pattern; mirrors Drive's ACCEPTED_DATE_FORMATS order
# (Drive validates the dateFormat string against this list). Names are readable identifiers.

class DateFormat(str, Enum):
    YYYY_DASH_MM_DASH_DD = "yyyy-MM-dd"
    YYYY_SLASH_MM_SLASH_DD = "yyyy/MM/dd"
    MM_DASH_DD_DASH_YYYY = "MM-dd-yyyy"
    MM_SLASH_DD_SLASH_YYYY = "MM/dd/yyyy"
    M_SLASH_DD_SLASH_YYYY = "M/dd/yyyy"
    MM_DASH_DD_DASH_YY = "MM-dd-yy"
    MM_SLASH_DD_SLASH_YY = "MM/dd/yy"
    M_SLASH_DD_SLASH_YY = "M/dd/yy"
    M_SLASH_D_SLASH_YY = "M/d/yy"
    DD_DASH_MM_DASH_YYYY = "dd-MM-yyyy"
    DD_SLASH_MM_SLASH_YYYY = "dd/MM/yyyy"
    DD_SLASH_M_SLASH_YYYY = "dd/M/yyyy"
    DD_DASH_MM_DASH_YY = "dd-MM-yy"
    DD_SLASH_MM_SLASH_YY = "dd/MM/yy"
    DD_SLASH_M_SLASH_YY = "dd/M/yy"
    D_SLASH_M_SLASH_YY = "d/M/yy"
    M_SLASH_D_SLASH_YYYY = "M/d/yyyy"
    DD_DASH_MMM_DASH_YY = "dd-MMM-yy"
    DD_DASH_MMM_DASH_YYYY = "dd-MMM-yyyy"
    DD_MMM_YYYY = "dd MMM yyyy"
    D_MMM_YYYY = "d MMM yyyy"
    MMMM_DD_YYYY = "MMMM dd, yyyy"


# ── Sales handoff checklist dropdowns ──

class MigratingFrom(str, Enum):
    EXCEL = "Excel"
    ANOTHER_TOOL = "Another tool"
    NOT_MIGRATING = "Not migrating"


class IndiaSupportCommitment(str, Enum):
    COMMITTED = "Committed"
    BEST_EFFORT = "Best-effort"


class PocDone(str, Enum):
    YES = "Yes"
    NO = "No"


class PocAccessRemoved(str, Enum):
    YES = "Yes"
    NO = "No"
    NOT_APPLICABLE = "Not applicable"
