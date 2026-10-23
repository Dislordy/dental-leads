import datetime as dt

from leadgen import extract


def _encode_cf(email, key=0x42):
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in email)


def test_emails_from_mailto_cloudflare_and_text():
    html = f"""
    <a href="mailto:Info@SmithFamilyDental.com?subject=Hi">Email us</a>
    <span class="__cf_email__" data-cfemail="{_encode_cf('drsmith@smithfamilydental.com')}">[email protected]</span>
    <a href="/cdn-cgi/l/email-protection#{_encode_cf('frontdesk@smithfamilydental.com')}">x</a>
    <p>Write to office [at] smithfamilydental [dot] com</p>
    <img src="logo@2x.png"> <p>user@example.com</p>
    <script>var s="0123456789abcdef0123456789abcdef@sentry.wixpress.com"</script>
    """
    emails = extract.extract_emails(html)
    assert emails[0] == "info@smithfamilydental.com"
    assert "drsmith@smithfamilydental.com" in emails
    assert "frontdesk@smithfamilydental.com" in emails
    assert "office@smithfamilydental.com" in emails
    assert not any("example.com" in e or "wixpress" in e or "png" in e for e in emails)


def test_rank_email_prefers_dentist_on_own_domain():
    emails = ["careers@smithdental.com", "info@smithdental.com", "jsmith@smithdental.com", "x@gmail.com"]
    best = max(emails, key=lambda e: extract.rank_email(e, "smithdental.com", "Dr. Jane Smith"))
    assert best == "jsmith@smithdental.com"


def test_head_dentist_prefers_founder_and_merges_last_name_mentions():
    text = ("Welcome to Bright Creek Dental. Meet Dr. Jane A. Smith, DDS, founder and lead dentist. "
            "Dr. Smith opened the practice in 2023. Our associate Dr. Mark Lee joined in 2024. "
            "Dr. Smith loves helping families. Our Team includes hygienist Amy Cole, RDH.")
    mentions = list(extract.find_dentist_mentions(text, "Meet Dr. Jane A. Smith", "https://x.com/meet-the-doctor"))
    head, count, ranked = extract.pick_head_dentist(mentions)
    assert head == "Dr. Jane Smith"
    assert count == 2
    assert [n for n, _ in ranked][:2] == ["Jane Smith", "Mark Lee"]


def test_dentist_name_stops_at_stopwords():
    mentions = list(extract.find_dentist_mentions("Call Dr. Patel Family Dentistry Plano today"))
    head, count, _ = extract.pick_head_dentist(mentions)
    assert head == "Dr. Patel"


def test_credential_suffix_name():
    mentions = list(extract.find_dentist_mentions("Our Office Meet Priya Raman, DMD. She graduated in 2015."))
    head, _, _ = extract.pick_head_dentist(mentions)
    assert head == "Dr. Priya Raman"


def test_established_year():
    today = dt.date(2026, 10, 1)
    assert extract.extract_established_year("Proudly serving Frisco since 2022!", today) == 2022
    assert extract.extract_established_year("Dr. Lee graduated in 2009. We opened our doors in 2024.", today) == 2024
    assert extract.extract_established_year("Est. 2021 | © 2026 Smile Co", today) == 2021
    assert extract.extract_established_year("Founded in 2031", today) is None
    assert extract.extract_established_year("Copyright 2025", today) is None


def test_classify_practice():
    assert extract.classify_practice("Frisco Kids Dentistry") == {"pediatric"}
    assert extract.classify_practice("Allen Orthodontics") == {"orthodontics"}
    t = extract.classify_practice("Bright Creek Family Dental", "cosmetic veneers and more cosmetic work")
    assert t == {"general", "family", "cosmetic"}
