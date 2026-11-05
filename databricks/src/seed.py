import argparse
import re
from datetime import date, timedelta
from decimal import Decimal


SCHEMA = 'construction_supply_demo'
DATASET = 'construction-supply-v1'
SNAPSHOT = date(2026, 11, 1)
TABLES = {
    'products': (
        'sku STRING, description STRING, category STRING, sell_uom STRING, '
        'base_uom STRING, base_units_per_sell_unit DECIMAL(12,4), is_mock BOOLEAN',
        ('sku',),
    ),
    'prices': (
        'price_id STRING, sku STRING, customer_tier STRING, min_qty INT, '
        'max_qty INT, unit_price DECIMAL(12,2), currency STRING, '
        'valid_from DATE, valid_to DATE, is_mock BOOLEAN',
        ('price_id',),
    ),
    'branches': (
        'branch_id STRING, branch_name STRING, delivery_region STRING, '
        'delivery_fee DECIMAL(12,2), free_delivery_min_subtotal DECIMAL(12,2), '
        'is_mock BOOLEAN',
        ('branch_id',),
    ),
    'inventory': (
        'sku STRING, branch_id STRING, on_hand INT, reserved INT, '
        'replenishment_days INT, snapshot_date DATE, is_mock BOOLEAN',
        ('sku', 'branch_id'),
    ),
    'order_lines': (
        'order_id STRING, line_number INT, order_date DATE, sku STRING, '
        'branch_id STRING, customer_tier STRING, quantity INT, '
        'unit_price DECIMAL(12,2), line_total DECIMAL(14,2), currency STRING, '
        'is_mock BOOLEAN',
        ('order_id', 'line_number'),
    ),
}


def build_data():
    catalog = [
        ('LUM-248', '2 x 4 x 8 ft SPF framing lumber', 'Lumber', 'BOARD', 'BOARD', '1', '4.85'),
        ('LUM-2410', '2 x 4 x 10 ft SPF framing lumber', 'Lumber', 'BOARD', 'BOARD', '1', '6.35'),
        ('PLY-1234', '3/4 in plywood 4 x 8 ft sheet', 'Panels', 'SHEET', 'SQFT', '32', '48.50'),
        ('OSB-716', '7/16 in OSB 4 x 8 ft sheet', 'Panels', 'SHEET', 'SQFT', '32', '18.75'),
        ('CON-80', '80 lb general-purpose concrete mix', 'Masonry', 'BAG', 'LB', '80', '7.40'),
        ('MOR-60', '60 lb type S mortar mix', 'Masonry', 'BAG', 'LB', '60', '8.20'),
        ('DRY-1248', '1/2 in gypsum board 4 x 8 ft', 'Drywall', 'SHEET', 'SQFT', '32', '14.25'),
        ('DRY-5848', '5/8 in type X gypsum board 4 x 8 ft', 'Drywall', 'SHEET', 'SQFT', '32', '19.60'),
        ('SCR-3', '3 in coated deck screws box of 100', 'Fasteners', 'BOX', 'EACH', '100', '12.90'),
        ('NAIL-16', '16d framing nails 5 lb box', 'Fasteners', 'BOX', 'LB', '5', '11.50'),
        ('INS-R13', 'R13 insulation batt pack covering 106.56 sq ft', 'Insulation', 'PACK', 'SQFT', '106.56', '62.00'),
        ('PVC-3410', '3/4 in schedule 40 PVC pipe 10 ft', 'Plumbing', 'LENGTH', 'FT', '10', '5.80'),
    ]
    data = {name: [] for name in TABLES}
    for sku, description, category, sell_uom, base_uom, conversion, retail in catalog:
        data['products'].append(dict(
            sku=sku, description=description, category=category, sell_uom=sell_uom,
            base_uom=base_uom, base_units_per_sell_unit=Decimal(conversion), is_mock=True,
        ))
        for tier, tier_factor in [('RETAIL', '1'), ('CONTRACTOR', '.92'), ('NATIONAL', '.85')]:
            for minimum, maximum, qty_factor in [(1, 99, '1'), (100, None, '.95')]:
                for start, end, period_factor in [
                    (date(2026, 1, 1), date(2026, 6, 30), '.97'),
                    (date(2026, 7, 1), date(2026, 12, 31), '1'),
                ]:
                    price = (Decimal(retail) * Decimal(tier_factor) * Decimal(qty_factor)
                             * Decimal(period_factor)).quantize(Decimal('.01'))
                    data['prices'].append(dict(
                        price_id=f'{sku}-{tier}-{minimum}-{start.isoformat()}', sku=sku,
                        customer_tier=tier, min_qty=minimum, max_qty=maximum,
                        unit_price=price, currency='USD', valid_from=start, valid_to=end,
                        is_mock=True,
                    ))
    for branch_id, branch_name, region, fee, threshold in [
        ('NORTH', 'Demo North Yard', 'North demo zone', '65.00', '1500.00'),
        ('SOUTH', 'Demo South Yard', 'South demo zone', '85.00', '2000.00'),
    ]:
        data['branches'].append(dict(
            branch_id=branch_id, branch_name=branch_name, delivery_region=region,
            delivery_fee=Decimal(fee), free_delivery_min_subtotal=Decimal(threshold),
            is_mock=True,
        ))
        for index, product in enumerate(data['products']):
            data['inventory'].append(dict(
                sku=product['sku'], branch_id=branch_id,
                on_hand=0 if index == 9 else 140 + index * 17,
                reserved=0 if index == 9 else index * 3,
                replenishment_days=2 + index % 6, snapshot_date=SNAPSHOT, is_mock=True,
            ))
    for index in range(90):
        product = data['products'][index % len(catalog)]
        tier = ['RETAIL', 'CONTRACTOR', 'NATIONAL'][index % 3]
        quantity = [10, 50, 100, 125, 200][index % 5]
        ordered = SNAPSHOT - timedelta(days=90 - index)
        price = select_price(data['prices'], product['sku'], tier, quantity, ordered)
        data['order_lines'].append(dict(
            order_id=f'DEMO-{index + 1:04d}', line_number=1, order_date=ordered,
            sku=product['sku'], branch_id=['NORTH', 'SOUTH'][index % 2],
            customer_tier=tier, quantity=quantity, unit_price=price,
            line_total=price * quantity, currency='USD', is_mock=True,
        ))
    return data


def select_price(prices, sku, customer_tier, quantity, as_of):
    matches = [row for row in prices if row['sku'] == sku
               and row['customer_tier'] == customer_tier
               and row['min_qty'] <= quantity
               and (row['max_qty'] is None or quantity <= row['max_qty'])
               and row['valid_from'] <= as_of <= row['valid_to']]
    if len(matches) != 1:
        raise ValueError('Expected exactly one valid quantity/tier/date price')
    return matches[0]['unit_price']


def seed(spark, catalog):
    if not re.fullmatch(r'[a-z][a-z0-9_]*', catalog):
        raise ValueError('Catalog must be a simple lowercase SQL identifier')
    namespace = f'`{catalog}`.`{SCHEMA}`'
    spark.sql(f'CREATE SCHEMA IF NOT EXISTS {namespace}')
    for table, rows in build_data().items():
        ddl, keys = TABLES[table]
        qualified = f'{namespace}.`{table}`'
        spark.sql(
            f"CREATE TABLE IF NOT EXISTS {qualified} ({ddl}) USING DELTA "
            f"COMMENT 'Synthetic construction supplies. Not real quotes or stock.' "
            f"TBLPROPERTIES ('demo.dataset' = '{DATASET}')"
        )
        marker = spark.sql(f"SHOW TBLPROPERTIES {qualified} ('demo.dataset')").first()
        if marker is None or marker['value'] != DATASET:
            raise ValueError(f'Refusing to modify a table not owned by this demo: {table}')
        spark.createDataFrame(rows, ddl).createOrReplaceTempView(f'demo_seed_{table}')
        predicate = ' AND '.join(f'target.`{key}` = source.`{key}`' for key in keys)
        spark.sql(
            f'MERGE INTO {qualified} target USING demo_seed_{table} source ON {predicate} '
            'WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *'
        )
        print(f'{table}: merged {len(rows)} synthetic records')


if __name__ == '__main__':
    from pyspark.sql import SparkSession

    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', required=True)
    options = parser.parse_args()
    seed(SparkSession.builder.getOrCreate(), options.catalog)