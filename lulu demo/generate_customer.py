import pandas as pd
import random
import string
from datetime import datetime, timedelta

start_date = datetime(2021, 11, 1)
end_date = datetime(2025, 7, 28)
delta = end_date - start_date

num_customers = 2000

data = {
    "customer_code": ["C" + "".join(random.choices(string.digits, k=8)) + str(i) for i in range(num_customers)],
    "register_time": [start_date + timedelta(days=random.randint(0, delta.days)) for _ in range(num_customers)],
    "most_often_purchased_city_tier": random.choices([1, 1.5, 2, 3], [0.4, 0.3, 0.15, 0.15], k=num_customers),
    "gender": random.choices(["Men", "Women", None], [0.1, 0.4, 0.5], k=num_customers),
    "if_binded_with_wecom": random.choices([0, 1], [0.52, 0.48], k=num_customers),
    "num_times_participated_community": random.choices(range(0, 100), [0.98] + [0.02 * i / sum(range(1, 100)) for i in range(99, 0, -1)], k=num_customers)
}
df = pd.DataFrame(data)

df.to_csv("guest_tagging.csv", index=False)