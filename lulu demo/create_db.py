import pandas as pd
from sqlalchemy import create_engine, text
import pandas as pd
import re

uri = "mysql+pymysql://root:jMoLJjYYyaXOcVRFGJszweAEqMIRxiHT@switchback.proxy.rlwy.net:49331/railway"

engine = create_engine(uri)

# with engine.connect() as conn:
#     conn.execute(
        # text(
        #     """
        #     DROP TABLE transaction
        #     """
        # )

        # text(
        #     """
        #     CREATE TABLE IF NOT EXISTS guest_tagging (
        #         customer_code VARCHAR(50) PRIMARY KEY,
        #         register_time DATE,
        #         most_often_purchased_city_tier REAL,
        #         gender VARCHAR(10),
        #         if_binded_with_wecom INT,
        #         num_times_participated_community INT
        #     )
        #     """
        # )

        # text(
        #     """
        #     CREATE TABLE IF NOT EXISTS product_master (
        #         sku_code VARCHAR(50) PRIMARY KEY,
        #         division_name VARCHAR(50),
        #         department_name VARCHAR(50),
        #         class_name VARCHAR(50),
        #         collection VARCHAR(100),
        #         designed_for_activity VARCHAR(50)
        #     )
        #     """
        # )

    #     text(
    #         """
    #         CREATE TABLE transaction (
    #             customer_code VARCHAR(50),
    #             paid_date DATE,
    #             channel VARCHAR(10),
    #             subchannel VARCHAR(10),
    #             sku_code VARCHAR(50),
    #             FOREIGN KEY (customer_code) REFERENCES guest_tagging(customer_code),
    #             FOREIGN KEY (sku_code) REFERENCES product_master(sku_code)
    #         )
    #         """
    #     )
    # )

df_customer = pd.read_csv("guest_tagging.csv")
df_product = pd.read_csv("product_master.csv")
df_transaction = pd.read_csv("transaction.csv")

def clean(val):
    if pd.isna(val):
        return None
    return val

# customer_data = []
# for idx, row in df_customer.iterrows():
#     customer_data.append(
#         {
#             "customer_code": clean(row["customer_code"]),
#             "register_time": clean(row["register_time"]),
#             "most_often_purchased_city_tier": clean(row["most_often_purchased_city_tier"]),
#             "gender": clean(row["gender"]),
#             "if_binded_with_wecom": clean(row["if_binded_with_wecom"]),
#             "num_times_participated_community": clean(row["num_times_participated_community"])
#         }
#     )

# insert_customers = text("""
#     INSERT INTO guest_tagging (
#         customer_code, register_time, most_often_purchased_city_tier, gender, if_binded_with_wecom, num_times_participated_community
#     ) VALUES (
#         :customer_code, :register_time, :most_often_purchased_city_tier, :gender, :if_binded_with_wecom, :num_times_participated_community
#     )
# """)

# with engine.begin() as conn:
#     conn.execute(insert_customers, customer_data)
# exit()

# product_data = []
# for idx, row in df_product.iterrows():
#     product_data.append(
#         {
#             "sku_code": clean(row["sku_code"]),
#             "division_name": clean(row["division_name"]),
#             "department_name": clean(row["department_name"]),
#             "class_name": clean(row["class_name"]),
#             "collection": clean(row["collection"]),
#             "designed_for_activity": clean(row["designed_for_activity"])
#         }
#     )

# insert_products = text("""
#     INSERT INTO product_master (
#         sku_code, division_name, department_name, class_name, collection, designed_for_activity
#     ) VALUES (
#         :sku_code, :division_name, :department_name, :class_name, :collection, :designed_for_activity
#     )
# """)

# with engine.begin() as conn:
#     conn.execute(insert_products, product_data)
# exit()

transaction_data = []
for idx, row in df_transaction.iterrows():
    transaction_data.append(
        {
            "customer_code": clean(row["customer_code"]),
            "paid_date": clean(row["paid_date"]),
            "channel": clean(row["channel"]),
            "subchannel": clean(row["subchannel"]),
            "sku_code": clean(row["sku_code"])
        }
    )

insert_transactions = text("""
    INSERT INTO transaction (
        customer_code, paid_date, channel, subchannel, sku_code
    ) VALUES (
        :customer_code, :paid_date, :channel, :subchannel, :sku_code
    )
""")

with engine.begin() as conn:
    conn.execute(insert_transactions, transaction_data)