-- Every delivery day in the feature mart has all of its 23, 24 or 25 hours.

select delivery_date, any_value(hours_in_day) as hours_in_day, count(*) as hours
from {{ ref('ml_features_hourly') }}
group by delivery_date
having count(*) <> any_value(hours_in_day)
