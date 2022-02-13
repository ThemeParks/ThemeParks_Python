# openapi_client.DestinationsApi

All URIs are relative to *https://api.themeparks.wiki/v1*

Method | HTTP request | Description
------------- | ------------- | -------------
[**get_destinations**](DestinationsApi.md#get_destinations) | **GET** /destinations | Get a list of supported destinations available on the live API


# **get_destinations**
> DestinationsResponse get_destinations()

Get a list of supported destinations available on the live API

### Example


```python
import time
import openapi_client
from openapi_client.api import destinations_api
from openapi_client.model.destinations_response import DestinationsResponse
from pprint import pprint
# Defining the host is optional and defaults to https://api.themeparks.wiki/v1
# See configuration.py for a list of all supported configuration parameters.
configuration = openapi_client.Configuration(
    host = "https://api.themeparks.wiki/v1"
)


# Enter a context with an instance of the API client
with openapi_client.ApiClient() as api_client:
    # Create an instance of the API class
    api_instance = destinations_api.DestinationsApi(api_client)

    # example, this endpoint has no required or optional parameters
    try:
        # Get a list of supported destinations available on the live API
        api_response = api_instance.get_destinations()
        pprint(api_response)
    except openapi_client.ApiException as e:
        print("Exception when calling DestinationsApi->get_destinations: %s\n" % e)
```


### Parameters
This endpoint does not need any parameter.

### Return type

[**DestinationsResponse**](DestinationsResponse.md)

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

