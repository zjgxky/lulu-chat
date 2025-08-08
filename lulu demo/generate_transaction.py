import pandas as pd
from datetime import datetime, timedelta
import random

df_customer = pd.read_csv("guest_tagging.csv")
customer_code_pre_24 = df_customer[df_customer["register_time"] <= str(datetime(2024, 1, 28))]["customer_code"].to_list()
customer_code_pre_25 = df_customer[df_customer["register_time"] <= str(datetime(2025, 2, 2))]["customer_code"].to_list()
customer_code_all = df_customer["customer_code"].to_list()

customer_23 = random.sample(customer_code_pre_24, k=600)
customer_24 = random.sample(customer_code_pre_25, k=1000)
customer_25 = random.sample(customer_code_all, k=800)

df_penetration = pd.read_csv("Collection raw data - collection guest penetration.csv")
df_product = pd.read_csv("product_master.csv")

penetration_list = df_penetration[df_penetration["Collection"].isin(df_penetration["Collection"][:50].tolist() + [
    'Bags - Wunderlust', 'Bags - Fast Track', 'Bottle', 'Bags - On My Level Bag', 'Bags - City Adventurer', 'Bags - Everywhere'
])]["Collection"].to_list()
penetration_freq = df_penetration[df_penetration["Collection"].isin(df_penetration["Collection"][:50].tolist() + [
    'Bags - Wunderlust', 'Bags - Fast Track', 'Bottle', 'Bags - On My Level Bag', 'Bags - City Adventurer', 'Bags - Everywhere'
])]["Guest Penetration"].to_list()
penetration_freq = [float(freq.rstrip("%")) * 0.01 for freq in penetration_freq]
penetration_freq = [freq / sum(penetration_freq) for freq in penetration_freq]

customer_code_list = []
paid_date_list = []
channel_list = []
subchannel_list = []
sku_code_list = []

for idx, customer in enumerate(customer_23 + customer_24 + customer_25):
    purchase_time = random.choices(["OT", "2-4", "5-11", "12+"], [0.48, 0.34, 0.13, 0.05], k=1)[0]
    if purchase_time == "OT":
        purchase_time = 1
    elif purchase_time == "2-4":
        purchase_time = random.choices(range(2, 5), k=1)[0]
    elif purchase_time == "5-11":
        purchase_time = random.choices(range(5, 12), k=1)[0]
    elif purchase_time == "12+":
        purchase_time = random.choices(range(12, 100), [i / sum(range(12, 100)) for i in range(99, 11, -1)], k=1)[0]
    
    for _ in range(purchase_time):
        customer_code_list.append(customer)

        if idx < 600:
            start_date = datetime(2023, 1, 30)
            end_date = datetime(2024, 1, 28)
            delta = end_date - start_date
        elif 600 < idx < 1600:
            start_date = datetime(2024, 1, 29)
            end_date = datetime(2025, 2, 2)
            delta = end_date - start_date
        elif 1600 < idx:
            start_date = datetime(2025, 2, 3)
            end_date = datetime(2025, 7, 28)
            delta = end_date - start_date
        paid_date_list.append(start_date + timedelta(days=random.randint(0, delta.days)))

        channel = random.choices(["Retail", "EC"], k=1)[0]
        channel_list.append(channel)
        
        if channel == "Retail":
            subchannel_list.append("Store")
        else:
            subchannel_list.append(random.choices(["MPS", "Tmall", "JD", "Douyin", ".CN"], k=1)[0])

        collection = random.choices(penetration_list, penetration_freq, k=1)[0]
        sku_code_list.append(random.choices(df_product[df_product["collection"] == collection]["sku_code"].to_list(), k=1)[0])

data = {
    "customer_code": customer_code_list,
    "paid_date": paid_date_list,
    "channel": channel_list,
    "subchannel": subchannel_list,
    "sku_code": sku_code_list
}
df = pd.DataFrame(data)
# shuffled_df = df.sample(frac=1).reset_index(drop=True)

df.to_csv("transaction.csv", index=False)