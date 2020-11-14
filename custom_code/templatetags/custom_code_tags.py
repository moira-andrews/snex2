from plotly import offline
import plotly.graph_objs as go
from django import template
from django.conf import settings
from django.db.models.functions import Lower
from django.shortcuts import reverse
from guardian.shortcuts import get_objects_for_user, get_perms
from django.contrib.auth.models import User, Group

from tom_targets.models import Target, TargetExtra
from tom_targets.forms import TargetVisibilityForm
from tom_observations import utils, facility
from tom_dataproducts.models import DataProduct, ReducedDatum, ObservationRecord

from astroplan import Observer, FixedTarget, AtNightConstraint, time_grid_from_range, moon_illumination
import datetime
import json
from astropy.time import Time
from astropy import units as u
from astropy.coordinates import get_moon, get_sun, SkyCoord, AltAz
import numpy as np
import time

from custom_code.models import ScienceTags, TargetTags, ReducedDatumExtra
from custom_code.forms import CustomDataProductUploadForm
from urllib.parse import urlencode

register = template.Library()

@register.inclusion_tag('custom_code/airmass_collapse.html')
def airmass_collapse(target):
    interval = 30 #min
    airmass_limit = 3.0

    obj = Target
    obj.ra = target.ra
    obj.dec = target.dec
    obj.epoch = 2000
    obj.type = 'SIDEREAL' 

    plot_data = get_24hr_airmass(obj, interval, airmass_limit)
    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(range=[airmass_limit,1.0],gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=20,r=10,b=30,t=40),
        hovermode='closest',
        width=250,
        height=200,
        showlegend=False,
        plot_bgcolor='white'
    )
    visibility_graph = offline.plot(
            go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False, config={'staticPlot': True}, include_plotlyjs='cdn'
    )
    return {
        'target': target,
        'figure': visibility_graph
    }

@register.inclusion_tag('custom_code/airmass.html', takes_context=True)
def airmass_plot(context):
    #request = context['request']
    interval = 15 #min
    airmass_limit = 3.0
    plot_data = get_24hr_airmass(context['object'], interval, airmass_limit)
    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(range=[airmass_limit,1.0],gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=20,r=10,b=30,t=40),
        hovermode='closest',
        width=600,
        height=300,
        plot_bgcolor='white'
    )
    visibility_graph = offline.plot(
        go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False
    )
    return {
        'target': context['object'],
        'figure': visibility_graph
    }

def get_24hr_airmass(target, interval, airmass_limit):

    plot_data = []
    
    start = Time(datetime.datetime.utcnow())
    end = Time(start.datetime + datetime.timedelta(days=1))
    time_range = time_grid_from_range(
        time_range = [start, end],
        time_resolution = interval*u.minute)
    time_plot = time_range.datetime
    
    fixed_target = FixedTarget(name = target.name, 
        coord = SkyCoord(
            target.ra,
            target.dec,
            unit = 'deg'
        )
    )

    #Hack to speed calculation up by factor of ~3
    sun_coords = get_sun(time_range[int(len(time_range)/2)])
    fixed_sun = FixedTarget(name = 'sun',
        coord = SkyCoord(
            sun_coords.ra,
            sun_coords.dec,
            unit = 'deg'
        )
    )

    #Colors to match SNEx1
    colors = {
        'Siding Spring': '#3366cc',
        'Sutherland': '#dc3912',
        'Teide': '#8c6239',
        'Cerro Tololo': '#ff9900',
        'McDonald': '#109618',
        'Haleakala': '#990099'
    }

    for observing_facility in facility.get_service_classes():

        if observing_facility != 'LCO':
            continue

        observing_facility_class = facility.get_service_class(observing_facility)
        sites = observing_facility_class().get_observing_sites()

        for site, site_details in sites.items():

            observer = Observer(
                longitude = site_details.get('longitude')*u.deg,
                latitude = site_details.get('latitude')*u.deg,
                elevation = site_details.get('elevation')*u.m
            )
            
            sun_alt = observer.altaz(time_range, fixed_sun).alt
            obj_airmass = observer.altaz(time_range, fixed_target).secz

            bad_indices = np.argwhere(
                (obj_airmass >= airmass_limit) |
                (obj_airmass <= 1) |
                (sun_alt > -18*u.deg)  #between astro twilights
            )

            obj_airmass = [np.nan if i in bad_indices else float(x)
                for i, x in enumerate(obj_airmass)]

            label = '({facility}) {site}'.format(
                facility = observing_facility, site = site
            )

            plot_data.append(
                go.Scatter(x=time_plot, y=obj_airmass, mode='lines', name=label, marker=dict(color=colors[site]))
            )

    return plot_data


def get_color(filter_name):
    filter_translate = {'U': 'U', 'B': 'B', 'V': 'V',
        'g': 'g', 'gp': 'g', 'r': 'r', 'rp': 'r', 'i': 'i', 'ip': 'i',
        'g_ZTF': 'g_ZTF', 'r_ZTF': 'r_ZTF', 'i_ZTF': 'i_ZTF', 'UVW2': 'UVW2', 'UVM2': 'UVM2', 
        'UVW1': 'UVW1'}
    colors = {'U': 'rgb(59,0,113)',
        'B': 'rgb(0,87,255)',
        'V': 'rgb(120,255,0)',
        'g': 'rgb(0,204,255)',
        'r': 'rgb(255,124,0)',
        'i': 'rgb(144,0,43)',
        'g_ZTF': 'rgb(0,204,255)',
        'r_ZTF': 'rgb(255,124,0)',
        'i_ZTF': 'rgb(144,0,43)',
        'UVW2': '#FE0683',
        'UVM2': '#BF01BC',
        'UVW1': '#8B06FF',
        'other': 'rgb(0,0,0)'}
    try: color = colors[filter_translate[filter_name]]
    except: color = colors['other']
    return color


@register.inclusion_tag('custom_code/lightcurve.html', takes_context=True)
def lightcurve(context, target):
         
    photometry_data = {}

    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])
    else:
        datums = get_objects_for_user(context['request'].user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]))

    for rd in datums:
    #for rd in ReducedDatum.objects.filter(target=target, data_type='photometry'):
        value = json.loads(rd.value)
        if not value:  # empty
            continue
   
        photometry_data.setdefault(value.get('filter', ''), {})
        photometry_data[value.get('filter', '')].setdefault('time', []).append(rd.timestamp)
        photometry_data[value.get('filter', '')].setdefault('magnitude', []).append(value.get('magnitude',None))
        photometry_data[value.get('filter', '')].setdefault('error', []).append(value.get('error', None))        

    plot_data = [
        go.Scatter(
            x=filter_values['time'],
            y=filter_values['magnitude'], mode='markers',
            marker=dict(color=get_color(filter_name)),
            name=filter_name,
            error_y=dict(
                type='data',
                array=filter_values['error'],
                visible=True,
                color=get_color(filter_name)
            )
        ) for filter_name, filter_values in photometry_data.items()] 
     

    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=30, r=10, b=100, t=40),
        hovermode='closest',
        plot_bgcolor='white'
        #height=500,
        #width=500
    )
    if plot_data:
      return {
          'target': target,
          'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False)
      }
    else:
        return {
            'target': target,
            'plot': 'No photometry for this target yet.'
        }


@register.inclusion_tag('custom_code/lightcurve_collapse.html')
def lightcurve_collapse(target, user):
         
    photometry_data = {}
    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])
    else:
        datums = get_objects_for_user(user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]))
    #for rd in ReducedDatum.objects.filter(target=target, data_type='photometry'): 
    for rd in datums:
        value = json.loads(rd.value)
        photometry_data.setdefault(value.get('filter', ''), {})
        photometry_data[value.get('filter', '')].setdefault('time', []).append(rd.timestamp)
        photometry_data[value.get('filter', '')].setdefault('magnitude', []).append(value.get('magnitude',None))
        photometry_data[value.get('filter', '')].setdefault('error', []).append(value.get('error', None))
    plot_data = [
        go.Scatter(
            x=filter_values['time'],
            y=filter_values['magnitude'], mode='markers',
            marker=dict(color=get_color(filter_name)),
            error_y=dict(
                type='data',
                array=filter_values['error'],
                visible=True,
                color=get_color(filter_name)
            )
        ) for filter_name, filter_values in photometry_data.items()]
    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=30, r=10, b=30, t=40),
        hovermode='closest',
        height=200,
        width=250,
        showlegend=False,
        plot_bgcolor='white'
    )
    if plot_data:
        return {
            'target': target,
            'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False, config={'staticPlot': True}, include_plotlyjs='cdn')
        }
    else:
        return {
            'target': target,
            'plot': 'No photometry for this target yet.'
        }

@register.inclusion_tag('custom_code/moon.html')
def moon_vis(target):

    day_range = 30
    times = Time(
        [str(datetime.datetime.utcnow() + datetime.timedelta(days=delta))
            for delta in np.arange(0, day_range, 0.2)],
        format = 'iso', scale = 'utc'
    )
    
    obj_pos = SkyCoord(target.ra, target.dec, unit=u.deg)
    moon_pos = get_moon(times)

    separations = moon_pos.separation(obj_pos).deg
    phases = moon_illumination(times)

    distance_color = 'rgb(0, 0, 255)'
    phase_color = 'rgb(255, 0, 0)'
    plot_data = [
        go.Scatter(x=times.mjd-times[0].mjd, y=separations, 
            mode='lines',name='Moon distance (degrees)',
            line=dict(color=distance_color)
        ),
        go.Scatter(x=times.mjd-times[0].mjd, y=phases, 
            mode='lines', name='Moon phase', yaxis='y2',
            line=dict(color=phase_color))
    ]
    layout = go.Layout(
        xaxis=dict(title='Days from now'),
        yaxis=dict(range=[0.,180.],tick0=0.,dtick=45.,
            tickfont=dict(color=distance_color)
        ),
        yaxis2=dict(range=[0., 1.], tick0=0., dtick=0.25, overlaying='y', side='right',
            tickfont=dict(color=phase_color)),
        margin=dict(l=20,r=10,b=30,t=40),
        #hovermode='compare',
        width=600,
        height=300,
        autosize=True
    )
    figure = offline.plot(
        go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False
    )
   
    return {'plot': figure}

@register.inclusion_tag('custom_code/spectra.html')
def spectra_plot(target, dataproduct=None):
    spectra = []
    spectral_dataproducts = ReducedDatum.objects.filter(target=target, data_type='spectroscopy')
    if dataproduct:
        spectral_dataproducts = DataProduct.objects.get(dataproduct=dataproduct)
    for spectrum in spectral_dataproducts:
        datum = json.loads(spectrum.value)
        wavelength = []
        flux = []
        name = str(spectrum.timestamp).split(' ')[0]
        for key, value in datum.items():
            wavelength.append(value['wavelength'])
            flux.append(float(value['flux']))
        spectra.append((wavelength, flux, name))
    plot_data = [
        go.Scatter(
            x=spectrum[0],
            y=spectrum[1],
            name=spectrum[2]
        ) for spectrum in spectra]
    layout = go.Layout(
        height=600,
        width=700,
        hovermode='closest',
        xaxis=dict(
            tickformat="d",
            title='Wavelength (angstroms)',
            gridcolor='#D3D3D3',
            showline=True,
            linecolor='#D3D3D3',
            mirror=True
        ),
        yaxis=dict(
            tickformat=".1eg",
            title='Flux',
            gridcolor='#D3D3D3',
            showline=True,
            linecolor='#D3D3D3',
            mirror=True
        ),
        plot_bgcolor='white'
    )
    if plot_data:
      return {
          'target': target,
          'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False)
      }
    else:
        return {
            'target': target,
            'plot': 'No spectra for this target yet.'
        }

@register.inclusion_tag('custom_code/spectra_collapse.html')
def spectra_collapse(target):
    spectra = []
    spectral_dataproducts = ReducedDatum.objects.filter(target=target, data_type='spectroscopy')
    for spectrum in spectral_dataproducts:
        datum = json.loads(spectrum.value)
        wavelength = []
        flux = []
        for key, value in datum.items():
            wavelength.append(value['wavelength'])
            flux.append(float(value['flux']))
        spectra.append((wavelength, flux))
    plot_data = [
        go.Scatter(
            x=spectrum[0],
            y=spectrum[1]
        ) for spectrum in spectra]
    layout = go.Layout(
        height=200,
        width=250,
        margin=dict(l=30, r=10, b=30, t=40),
        showlegend=False,
        xaxis=dict(
            gridcolor='#D3D3D3',
            showline=True,
            linecolor='#D3D3D3',
            mirror=True
        ),
        yaxis=dict(
            showticklabels=False,
            gridcolor='#D3D3D3',
            showline=True,
            linecolor='#D3D3D3',
            mirror=True
        ),
        plot_bgcolor='white'
    )
    if plot_data:
      return {
          'target': target,
          'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False, config={'staticPlot': True}, include_plotlyjs='cdn')
      }
    else:
        return {
            'target': target,
            'plot': 'No spectra for this target yet.'
        }

@register.inclusion_tag('custom_code/aladin_collapse.html')
def aladin_collapse(target):
    return {'target': target}

@register.filter
def get_targetextra_id(target, keyword):
    try:
        targetextra = TargetExtra.objects.get(target_id=target.id, key=keyword)
        return targetextra.id
    except:
        return json.dumps(None)

@register.inclusion_tag('custom_code/classifications_dropdown.html')
def classifications_dropdown(target):
    classifications = [i for i in settings.TARGET_CLASSIFICATIONS]
    return {'target': target,
            'classifications': classifications}

@register.inclusion_tag('custom_code/science_tags_dropdown.html')
def science_tags_dropdown(target):
    tag_query = ScienceTags.objects.all().order_by(Lower('tag'))
    tags = [i.tag for i in tag_query]
    return{'target': target,
           'sciencetags': tags}

@register.filter
def get_target_tags(target):
    #try:
    target_tag_query = TargetTags.objects.filter(target_id=target.id)
    tags = ''
    for i in target_tag_query:
        tag_name = ScienceTags.objects.filter(id=i.tag_id).first().tag
        tags+=(str(tag_name) + ',')
    return json.dumps(tags)
    #except:
    #    return json.dumps(None)


@register.inclusion_tag('custom_code/custom_upload_dataproduct.html', takes_context=True)
def custom_upload_dataproduct(context, obj):
    user = context['user']
    initial = {}
    if isinstance(obj, Target):
        initial['target'] = obj
        initial['referrer'] = reverse('tom_targets:detail', args=(obj.id,))
    elif isinstance(obj, ObservationRecord):
        initial['observation_record'] = obj
        initial['referrer'] = reverse('tom_observations:detail', args=(obj.id,))
    form = CustomDataProductUploadForm(initial=initial)
    if not settings.TARGET_PERMISSIONS_ONLY:
        if user.is_superuser:
            form.fields['groups'].queryset = Group.objects.all()
        else:
            form.fields['groups'].queryset = user.groups.all()
    return {'data_product_form': form}


@register.inclusion_tag('custom_code/dash_lightcurve.html', takes_context=True)
def dash_lightcurve(context, target):
    request = context['request']
    
    # Get initial choices and values for some dash elements
    telescopes = ['LCO']
    reducer_groups = []
    papers_used_in = []

    dp_ids = []
    datumquery = ReducedDatum.objects.filter(target=target, data_type='photometry').order_by().values('data_product_id').distinct()
    for i in datumquery:
        dp_ids.append(i['data_product_id'])
    for de in ReducedDatumExtra.objects.filter(key='upload_extras', data_type='photometry'):
        de_value = json.loads(de.value)
        if de_value.get('data_product_id', '') in dp_ids:
            inst = de_value.get('instrument', '')
            used_in = de_value.get('used_in', '')
            group = de_value.get('reducer_group', '')

            if inst and inst not in telescopes:
                telescopes.append(inst)
            if used_in and used_in not in papers_used_in:
                papers_used_in.append(used_in)
            if group and group not in reducer_groups:
                reducer_groups.append(group)
    
    reducer_group_options = [{'label': 'LCO', 'value': ''}]
    reducer_group_options.extend([{'label': k, 'value': k} for k in reducer_groups])

    return {'dash_context': {'target_id': {'value': target.id},
                             'telescopes-checklist': {'options': [{'label': k, 'value': k} for k in telescopes]},
                             'reducer-group-checklist': {'options': reducer_group_options},
                             'papers-dropdown': {'options': [{'label': k, 'value': k} for k in papers_used_in]}},
            'request': request}


@register.inclusion_tag('custom_code/dataproduct_update.html')
def dataproduct_update(dataproduct):
    group_query = Group.objects.all()
    groups = [i.name for i in group_query]
    return{'dataproduct': dataproduct,
           'groups': groups}

@register.filter
def get_dataproduct_groups(dataproduct):
    # Query all the groups with permission for this dataproduct
    group_query = Group.objects.all()
    groups = ''
    for i in group_query:
        if 'view_dataproduct' in get_perms(i, dataproduct):
            groups += str(i.name) + ','
    return json.dumps(groups)
