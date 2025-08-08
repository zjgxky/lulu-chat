knowledge = """
Data Capture Rate: Number of transactions with associated guest information / Total number of transactions within the period of analysis

Unidentified Transaction: A positive or negative transaction with no captured guest information within the analysis period (orphan transactions); includes marketplace buyers with OUID etc. information

Registered profile: Registered with mobile on any of our channels

Prospects: Guest who registered with us without any positive or negative transaction

Purchased Guest: A guest who made a transaction (+/-) with guest information captured within the period of analysis

Active Guest: A guest who has made at least 1 positive or negative transaction within the period of analysis

Acquired (New) Guest: A guest who made their 1st transaction (+/-) with the brand within the period measured

Retained Guest: A registered guest who made a transaction (+/-) in year N and year N-1

Reactivated Guest: A registered guest who made a transaction (+/-) in year N and year N-2 or before and inactive in year N-1

Existing Guest: An existing guest (not New Guest as defined above) has made a transaction (+/-) before and within the analysis period

Net Sales: Shipped sales excluding returns & cancellations

Average Order Value (AOV): Shipped sales / Number of positive transactions within the analysis period

Frequency (FQY): Number of positive transactions / Number of Active Guests within the analysis period

Revenue Per Guest (RPG): Net Sales (excluding returns) / Number of Active Guests within the analysis period

Average Unit Retail (AUR): Average Revenue of placed units (Shipped sales / shipped units)

Unit Per Transaction (UPT): Unit Per Transaction (Shipped units / positive transactions only)
"""

with open("lulu_knowledge.txt", "w") as f:
    f.write(knowledge)