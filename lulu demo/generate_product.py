import pandas as pd
import random
import string

df_raw = pd.read_csv("Collection raw data - Sheet1.csv")
df_penetration = pd.read_csv("Collection raw data - collection guest penetration.csv")
df_select = df_raw[df_raw["Collection"].isin(df_penetration["Collection"][:50].tolist() + [
    'Bags - Wunderlust', 'Bags - Fast Track', 'Bottle', 'Bags - On My Level Bag', 'Bags - City Adventurer', 'Bags - Everywhere'
])]

data = {
    "sku_code": ["P" + "".join(random.choices(string.digits, k=8)) + str(i) for i in range(len(df_select))],
    "division_name": df_select["division_name"],
    "department_name": df_select["department_name"],
    "class_name": df_select["class_name"],
    "collection": df_select["Collection"],
    "designed_for_activity": df_select["designed_for_activity"]
}
df = pd.DataFrame(data)

df.to_csv("product_master.csv", index=False)