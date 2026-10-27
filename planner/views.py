import json
import uuid
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from planner.constants import US_STATES
from planner.models import Place
from planner.optimizer import NoFeasiblePlan
from planner.providers import ProviderUnavailable, RouteNotFound
from planner.services import DataUnavailable, InvalidInput, build_plan


@require_GET
def index(request):
    return render(request, 'planner/index.html', {'states': sorted(US_STATES)})


@require_POST
def route_plan(request):
    if request.content_type != 'application/json':
        return JsonResponse({'error': {'code': 'invalid_input', 'message': 'Use application/json.'}}, status=400)
    try:
        payload = json.loads(request.body)
        result = build_plan(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': {'code': 'invalid_input', 'message': 'Malformed JSON.'}}, status=400)
    except InvalidInput as exc:
        return JsonResponse({'error': {'code': 'invalid_input', 'message': str(exc)}}, status=400)
    except (NoFeasiblePlan, RouteNotFound) as exc:
        return JsonResponse({'error': {'code': 'no_verified_plan', 'message': str(exc)}}, status=422)
    except (ProviderUnavailable, DataUnavailable) as exc:
        response = JsonResponse({'error': {'code': 'service_unavailable', 'message': str(exc)}}, status=503)
        response['Retry-After'] = '5'
        return response
    plan_id = uuid.uuid4()
    result['map_url'] = f'/maps/{plan_id}/'
    result['map_expires_in_seconds'] = 86400
    cache.set(f'map:{plan_id}', result, timeout=86400)
    return JsonResponse(result)


@require_GET
def route_map(request, plan_id):
    plan = cache.get(f'map:{plan_id}')
    if plan is None:
        raise Http404('Map expired. Request the route again.')
    return render(request, 'planner/map.html', {'plan': plan})


@require_GET
def places(request):
    state = request.GET.get('state', '').strip().upper()
    if state not in US_STATES:
        return JsonResponse({'error': {'code': 'invalid_input', 'message': 'Choose a valid US state.'}}, status=400)
    keys = Place.objects.filter(key__endswith=f', {state}').order_by('key').values_list('key', flat=True)
    return JsonResponse({'cities': [{'value': key, 'label': f'{key.rsplit(", ", 1)[0].title()}, {state}'} for key in keys]})
