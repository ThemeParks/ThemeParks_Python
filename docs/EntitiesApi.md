# openapi_client.EntitiesApi

All URIs are relative to *https://api.themeparks.wiki/v1*

Method | HTTP request | Description
------------- | ------------- | -------------
[**get_entity**](EntitiesApi.md#get_entity) | **GET** /entity/{entityID} | Get entity document
[**get_entity_children**](EntitiesApi.md#get_entity_children) | **GET** /entity/{entityID}/children | Get all children for a given entity document
[**get_entity_live_data**](EntitiesApi.md#get_entity_live_data) | **GET** /entity/{entityID}/live | Get live data for this entity and any child entities
[**get_entity_schedule_upcoming**](EntitiesApi.md#get_entity_schedule_upcoming) | **GET** /entity/{entityID}/schedule | Get entity schedule
[**get_entity_schedule_year_month**](EntitiesApi.md#get_entity_schedule_year_month) | **GET** /entity/{entityID}/schedule/{year}/{month} | Get entity schedule for a specific month and year


# **get_entity**
> EntityData get_entity(entity_id)

Get entity document

Get the full data document for a given entity. You can supply either a GUID or slug string.

### Example


```python
import time
import openapi_client
from openapi_client.api import entities_api
from openapi_client.model.entity_data import EntityData
from pprint import pprint
# Defining the host is optional and defaults to https://api.themeparks.wiki/v1
# See configuration.py for a list of all supported configuration parameters.
configuration = openapi_client.Configuration(
    host = "https://api.themeparks.wiki/v1"
)


# Enter a context with an instance of the API client
with openapi_client.ApiClient() as api_client:
    # Create an instance of the API class
    api_instance = entities_api.EntitiesApi(api_client)
    entity_id = "entityID_example" # str | Entity ID (or slug) to fetch

    # example passing only required values which don't have defaults set
    try:
        # Get entity document
        api_response = api_instance.get_entity(entity_id)
        pprint(api_response)
    except openapi_client.ApiException as e:
        print("Exception when calling EntitiesApi->get_entity: %s\n" % e)
```


### Parameters

Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **entity_id** | **str**| Entity ID (or slug) to fetch |

### Return type

[**EntityData**](EntityData.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json


### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | successful fetch |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_entity_children**
> EntityChildrenResponse get_entity_children(entity_id)

Get all children for a given entity document

Fetch a list of all the children that belong to this entity. This is recursive, so a destination will return all parks and all rides within those parks.

### Example


```python
import time
import openapi_client
from openapi_client.api import entities_api
from openapi_client.model.entity_children_response import EntityChildrenResponse
from pprint import pprint
# Defining the host is optional and defaults to https://api.themeparks.wiki/v1
# See configuration.py for a list of all supported configuration parameters.
configuration = openapi_client.Configuration(
    host = "https://api.themeparks.wiki/v1"
)


# Enter a context with an instance of the API client
with openapi_client.ApiClient() as api_client:
    # Create an instance of the API class
    api_instance = entities_api.EntitiesApi(api_client)
    entity_id = "entityID_example" # str | Entity ID (or slug) to fetch

    # example passing only required values which don't have defaults set
    try:
        # Get all children for a given entity document
        api_response = api_instance.get_entity_children(entity_id)
        pprint(api_response)
    except openapi_client.ApiException as e:
        print("Exception when calling EntitiesApi->get_entity_children: %s\n" % e)
```


### Parameters

Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **entity_id** | **str**| Entity ID (or slug) to fetch |

### Return type

[**EntityChildrenResponse**](EntityChildrenResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json


### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | successful fetch |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_entity_live_data**
> EntityLiveDataResponse get_entity_live_data(entity_id)

Get live data for this entity and any child entities

Fetch this entity's live data (queue times, parade times, etc.) as well as all child entities. For a destination, this will include all parks within that destination.

### Example


```python
import time
import openapi_client
from openapi_client.api import entities_api
from openapi_client.model.entity_live_data_response import EntityLiveDataResponse
from pprint import pprint
# Defining the host is optional and defaults to https://api.themeparks.wiki/v1
# See configuration.py for a list of all supported configuration parameters.
configuration = openapi_client.Configuration(
    host = "https://api.themeparks.wiki/v1"
)


# Enter a context with an instance of the API client
with openapi_client.ApiClient() as api_client:
    # Create an instance of the API class
    api_instance = entities_api.EntitiesApi(api_client)
    entity_id = "entityID_example" # str | Entity ID (or slug) to fetch

    # example passing only required values which don't have defaults set
    try:
        # Get live data for this entity and any child entities
        api_response = api_instance.get_entity_live_data(entity_id)
        pprint(api_response)
    except openapi_client.ApiException as e:
        print("Exception when calling EntitiesApi->get_entity_live_data: %s\n" % e)
```


### Parameters

Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **entity_id** | **str**| Entity ID (or slug) to fetch |

### Return type

[**EntityLiveDataResponse**](EntityLiveDataResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json


### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | successful fetch |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_entity_schedule_upcoming**
> EntityScheduleResponse get_entity_schedule_upcoming(entity_id)

Get entity schedule

Fetch this entity's schedule for the next 30 days

### Example


```python
import time
import openapi_client
from openapi_client.api import entities_api
from openapi_client.model.entity_schedule_response import EntityScheduleResponse
from pprint import pprint
# Defining the host is optional and defaults to https://api.themeparks.wiki/v1
# See configuration.py for a list of all supported configuration parameters.
configuration = openapi_client.Configuration(
    host = "https://api.themeparks.wiki/v1"
)


# Enter a context with an instance of the API client
with openapi_client.ApiClient() as api_client:
    # Create an instance of the API class
    api_instance = entities_api.EntitiesApi(api_client)
    entity_id = "entityID_example" # str | Entity ID (or slug) to fetch

    # example passing only required values which don't have defaults set
    try:
        # Get entity schedule
        api_response = api_instance.get_entity_schedule_upcoming(entity_id)
        pprint(api_response)
    except openapi_client.ApiException as e:
        print("Exception when calling EntitiesApi->get_entity_schedule_upcoming: %s\n" % e)
```


### Parameters

Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **entity_id** | **str**| Entity ID (or slug) to fetch |

### Return type

[**EntityScheduleResponse**](EntityScheduleResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json


### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | successful fetch |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_entity_schedule_year_month**
> EntityScheduleResponse get_entity_schedule_year_month(entity_id, year, month)

Get entity schedule for a specific month and year

Fetch this entity's schedule for the supplied year and month

### Example


```python
import time
import openapi_client
from openapi_client.api import entities_api
from openapi_client.model.entity_schedule_response import EntityScheduleResponse
from pprint import pprint
# Defining the host is optional and defaults to https://api.themeparks.wiki/v1
# See configuration.py for a list of all supported configuration parameters.
configuration = openapi_client.Configuration(
    host = "https://api.themeparks.wiki/v1"
)


# Enter a context with an instance of the API client
with openapi_client.ApiClient() as api_client:
    # Create an instance of the API class
    api_instance = entities_api.EntitiesApi(api_client)
    entity_id = "entityID_example" # str | Entity ID (or slug) to fetch
    year = 3.14 # float | Schedule year to fetch
    month = 3.14 # float | Schedule month to fetch. Must be a two digit zero-padded month.

    # example passing only required values which don't have defaults set
    try:
        # Get entity schedule for a specific month and year
        api_response = api_instance.get_entity_schedule_year_month(entity_id, year, month)
        pprint(api_response)
    except openapi_client.ApiException as e:
        print("Exception when calling EntitiesApi->get_entity_schedule_year_month: %s\n" % e)
```


### Parameters

Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **entity_id** | **str**| Entity ID (or slug) to fetch |
 **year** | **float**| Schedule year to fetch |
 **month** | **float**| Schedule month to fetch. Must be a two digit zero-padded month. |

### Return type

[**EntityScheduleResponse**](EntityScheduleResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json


### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | successful fetch |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

