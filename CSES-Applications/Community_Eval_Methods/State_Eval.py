#!/usr/bin/env python
# coding: utf-8
#author: Ryan Johnson, PHD, Alabama Water Institute
#Date: 5-9-2024

#local packages
from Community_Eval_Methods import data
#Data Processing Modules
import pandas as pd
import numpy as np
import geopandas as gpd
from datetime import timedelta
import time
import jenkspy
import json
from geopy.geocoders import Nominatim

# Hydrological modeling utils
#from hydrotools.nwis_client.iv import IVDataService
from hydrotools.nwm_client import utils
import streamstats

#Plotting modules
import folium
import matplotlib
import matplotlib.pyplot as plt
import mpl_toolkits
from mpl_toolkits.mplot3d import Axes3D
import hvplot.pandas
import holoviews as hv
from holoviews import dim, opts, streams
from bokeh.models import HoverTool
import branca.colormap as cm
import vincent
from vincent import AxisProperties, PropertySet, ValueRef, Axis
import matplotlib.cm
from folium import features
import proplot as pplt
from folium.plugins import StripePattern

#Evaluation modules
from sklearn.metrics import r2_score
from sklearn.metrics import mean_squared_error
from sklearn.metrics import max_error
from sklearn.metrics import mean_absolute_percentage_error
import hydroeval as he

#AWS Data Access Modules
import boto3
from botocore import UNSIGNED
from botocore.client import Config

#General Environment modules
from IPython.display import display
import warnings
from progressbar import ProgressBar
import io
import os

#Environment settings/configs
pplt.rc["figure.facecolor"] = "w"
os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
geolocator = Nominatim(user_agent="geoapiExercises")
pd.options.plotting.backend = 'holoviews'
warnings.filterwarnings("ignore")





class LULC_Eval():
    
    def __init__(self, model ,state,  startDT, endDT, cwd):
        self = self
        #self.df =df
        self.startDT = startDT
        self.endDT = endDT
        self.cwd = cwd
        self.cms_to_cfs = 35.314666212661
        self.model = model
        self.state = state
        self.cfsday_AFday = 1.983
        self.freqkeys = {
                        'D': 'Daily',
                        'M': 'Monthly',
                        'Q': 'Quarterly',
                        'A': 'Annual'
                        }
        
    def get_NWIS(self):
        print('Getting NWIS Streamstats')
        
        #AWS bucket information
        bucket_name = 'streamflow-app-data'
        s3 = boto3.resource('s3', config=Config(signature_version=UNSIGNED))
        self.bucket = s3.Bucket(bucket_name)

       #Load streamstats wiht lat long to get geolocational information
        csv_key = 'Streamstats/Streamstats.csv'
        obj = self.bucket.Object(csv_key)
        body = obj.get()['Body']
        self.NWIS_sites = pd.read_csv(body)
        self.NWIS_sites.pop('Unnamed: 0')
        self.NWIS_sites.drop_duplicates(subset = 'NWIS_site_id', inplace = True)
        #self.NWIS_sites.set_index('NWIS_site_id', drop = True, inplace = True)
        self.NWIS_sites = self.NWIS_sites[self.NWIS_sites['state_id']==self.state.upper()]
        self.NWIS_sites.reset_index(inplace = True, drop = True)
        
         #the csv loses the 0 in front of USGS ids, fix
        NWIS = list(self.NWIS_sites['NWIS_site_id'].astype(str))
        self.NWIS_sites['NWIS_site_id'] = ["0"+str(i) if len(i) <8 else i for i in NWIS]
        
        #Make all NWIS sites correct 8 digit code
        for i in np.arange(0, len(self.NWIS_sites),1):
                    self.NWIS_sites.NWIS_site_id.loc[i] = str(self.NWIS_sites.NWIS_site_id.loc[i])
                    if len(self.NWIS_sites.NWIS_site_id.loc[i]) < 8:
                        self.NWIS_sites.NWIS_site_id.loc[i] = '0' + str(self.NWIS_sites.NWIS_site_id.loc[i])
                    else:
                        self.NWIS_sites.NWIS_site_id.loc[i] = str(self.NWIS_sites.NWIS_site_id.loc[i])


        #remove sites with not lat/long
        self.NWIS_sites = self.NWIS_sites[self.NWIS_sites['dec_lat_va'].notna()].reset_index()
        
        #remove "index" column
        self.NWIS_sites.pop('index')
        
        #convert to geodataframe
        self.NWIS_sites = gpd.GeoDataFrame(self.NWIS_sites, geometry=gpd.points_from_xy(self.NWIS_sites.dec_long_va, self.NWIS_sites.dec_lat_va))
        

    def get_NHD_Model_info(self):   
        print('Getting NHD reaches')
       #Get NHD reach colocated with NWIS
        self.site_id = self.NWIS_sites.NWIS_site_id
        
        NHD_reaches = []

        for site in self.site_id:
            try:
                NHD_NWIS_df = utils.crosswalk(usgs_site_codes=site)
                NHD_segment = NHD_NWIS_df.nwm_feature_id.values[0]
                NHD_reaches.append(NHD_segment)

            except:
                NHD_segment = np.nan
                NHD_reaches.append(NHD_segment)
        self.NWIS_sites['NHD_reachid'] = NHD_reaches
        
        self.NWIS_sites = self.NWIS_sites.fillna(0.0)
        
        self.NWIS_sites['NHD_reachid'] = self.NWIS_sites['NHD_reachid'].astype(int)
        
        self.NWIS_sites = self.NWIS_sites[self.NWIS_sites.NHD_reachid != 0]
        
        self.df = self.NWIS_sites.copy()
        
      


    def get_USGS_site_info(self, state):
        #url for state usgs id's
        url = 'https://waterdata.usgs.gov/'+state+'/nwis/current/?type=flow&group_key=huc_cd'

        NWIS_sites = pd.read_html(url)

        NWIS_sites = pd.DataFrame(np.array(NWIS_sites)[1]).reset_index(drop = True)

        cols = ['StationNumber', 'Station name','Date/Time','Gageheight, feet', 'Dis-charge, ft3/s']

        self.NWIS_sites = NWIS_sites[cols].dropna()
        
        self.NWIS_sites = self.NWIS_sites.rename(columns ={'Station name':'station_name', 
                                                               'Gageheight, feet': 'gageheight_ft',
                                                               'Dis-charge, ft3/s':'Discharge_cfs'})
        
        self.NWIS_sites = self.NWIS_sites[self.NWIS_sites.gageheight_ft != '--']


        self.NWIS_sites = self.NWIS_sites.set_index('StationNumber')
        
        
         # Remove unnecessary site information
        for i in self.NWIS_sites.index:
            if len(str(i)) > 8:
                self.NWIS_sites = self.NWIS_sites.drop(i)


        self.site_id = self.NWIS_sites.index

        #set up Pandas DF for state streamstats

        Streamstats_cols = ['NWIS_siteid', 'Drainage_area_mi2', 'Mean_Basin_Elev_ft', 'Perc_Forest', 'Perc_Develop',
                         'Perc_Imperv', 'Perc_Herbace', 'Perc_Slop_30', 'Mean_Ann_Precip_in']

        self.State_NWIS_Stats = pd.DataFrame(columns = Streamstats_cols)
        
        #set counter break to prevent blockage of Public IP address
        #count = 0
        
        print('Calculating NWIS streamflow id characteristics for ', len(self.site_id), ' sites in ', state)

        pbar = ProgressBar()
        for site in pbar(self.site_id):
            
            try:
                siteinfo = self.NWIS_sites['station_name'][site]

                print('Calculating the summary statistics of the catchment for ', siteinfo, ', USGS: ',site)
                NWISinfo = nwis.get_record(sites=site, service='site')

                lat, lon = NWISinfo['dec_lat_va'][0],NWISinfo['dec_long_va'][0]
                ws = streamstats.Watershed(lat=lat, lon=lon)

                NWISindex = ['NWIS_site_id', 'NWIS_sitename', 'Drainage_area_mi2', 'Mean_Basin_Elev_ft', 'Perc_Forest', 'Perc_Develop',
                             'Perc_Imperv', 'Perc_Herbace', 'Perc_Slop_30', 'Mean_Ann_Precip_in', 'Ann_low_cfs', 'Ann_mean_cfs', 'Ann_hi_cfs']


                #get stream statististics
                self.Param="00060"
                StartYr='1970'
                EndYr='2021'

                annual_stats = nwis.get_stats(sites=site,
                                      parameterCd=self.Param,
                                      statReportType='annual',
                                      startDt=StartYr,
                                      endDt=EndYr)

                mean_ann_low = annual_stats[0].nsmallest(1, 'mean_va')
                mean_ann_low = mean_ann_low['mean_va'].values[0]

                mean_ann = np.round(np.mean(annual_stats[0]['mean_va']),0)

                mean_ann_hi = annual_stats[0].nlargest(1, 'mean_va')
                mean_ann_hi = mean_ann_hi['mean_va'].values[0]


                try:
                    darea = ws.get_characteristic('DRNAREA')['value']
                except KeyError:
                    darea = np.nan
                except ValueError:
                    darea = np.nan

                try:
                    elev = ws.get_characteristic('ELEV')['value']
                except KeyError:
                    elev = np.nan
                except ValueError:
                    elev = np.nan

                try:
                    forest = ws.get_characteristic('FOREST')['value']
                except KeyError:
                    forest = np.nan
                except ValueError:
                    forest = np.nan

                try:
                    dev_area = ws.get_characteristic('LC11DEV')['value']
                except KeyError:
                    dev_area = np.nan
                except ValueError:
                    dev_area = np.nan

                try:
                    imp_area = ws.get_characteristic('LC11IMP')['value']
                except KeyError:
                    imp_area = np.nan
                except ValueError:
                    imp_area = np.nan

                try:
                    herb_area = ws.get_characteristic('LU92HRBN')['value']
                except KeyError:
                    herb_area = np.nan
                except ValueError:
                    herb_area = np.nan

                try:
                    perc_slope = ws.get_characteristic('SLOP30_10M')['value']
                except KeyError:
                    perc_slope = np.nan
                except ValueError:
                    perc_slope = np.nan

                try:
                    precip = ws.get_characteristic('PRECIP')['value']
                except KeyError:
                    precip = np.nan
                except ValueError:
                    precip = np.nan


                NWISvalues = [site,siteinfo, darea, elev,forest, dev_area, imp_area, herb_area, perc_slope, precip, mean_ann_low, mean_ann, mean_ann_hi]


                Catchment_Stats = pd.DataFrame(data = NWISvalues, index = NWISindex).T

                self.State_NWIS_Stats = self.State_NWIS_Stats.append(Catchment_Stats)
                
            except:
                time.sleep(181)
                print('Taking three minute break to prevent the blocking of IP Address') 
                
        colorder =[
    'NWIS_site_id',	'NWIS_sitename','Drainage_area_mi2','Mean_Basin_Elev_ft',
    'Perc_Forest', 'Perc_Develop','Perc_Imperv','Perc_Herbace','Perc_Slop_30',
    'Mean_Ann_Precip_in','Ann_low_cfs', 'Ann_mean_cfs','Ann_hi_cfs'
]

        del self.State_NWIS_Stats['NWIS_siteid']

        self.State_NWIS_Stats = self.State_NWIS_Stats[colorder]

        self.State_NWIS_Stats = self.State_NWIS_Stats.reset_index(drop = True)

        self.State_NWIS_Stats.to_csv(self.cwd+'/State_NWIS_StreamStats/StreamStats_'+state+'.csv')

        
        
    def class_eval_state(self, category):
        self.category = category
        self.cat_breaks = self.category+'_breaks'
        
        #remove rows with no value for category of interest
        self.df.drop(self.df[self.df[self.category]<0.00001].index, inplace = True)
            

        try: 
            breaks = jenkspy.jenks_breaks(self.df[self.category], n_classes=5)
            print('Categorical breaks for ', self.category, ': ',  breaks)
            self.df[self.cat_breaks] = pd.cut(self.df[self.category],
                                    bins=breaks,
                                    labels=['vsmall', 'small', 'medium', 'large', 'vlarge'],
                                                include_lowest=True)
            self.Catchment_Category()

        except ValueError:
            print('Not enough locations in this dataframe to categorize')    



        self.df = self.df.reset_index(drop = True)



    def Catchment_Category(self):
        #create df for each jenks category
        self.df_vsmall = self.df[self.df[self.cat_breaks]=='vsmall'].reset_index(drop = True)
        self.df_small = self.df[self.df[self.cat_breaks]=='small'].reset_index(drop = True)
        self.df_medium = self.df[self.df[self.cat_breaks]=='medium'].reset_index(drop = True)
        self.df_large= self.df[self.df[self.cat_breaks]=='large'].reset_index(drop = True)
        self.df_vlarge = self.df[self.df[self.cat_breaks]=='vlarge'].reset_index(drop = True)

    def NWIS_retrieve(self, df):
        # Retrieve data from a number of sites
        print('Retrieving USGS sites ', list(df.NWIS_site_id), ' data')
        self.NWIS_sites = list(df.NWIS_site_id)
        
        #self.NWIS_data = pd.DataFrame(columns = self.NWIS_sites)
        pbar = ProgressBar()
        for site in pbar(self.NWIS_sites):
            #print('Getting data for: ', site)
            
            try:
                service = IVDataService()
                usgs_data = service.get(
                    sites=str(site),
                    startDT= self.startDT,
                    endDT=self.endDT
                    )

                #Get Daily mean for Model comparision
                usgs_meanflow = pd.DataFrame(usgs_data.reset_index().groupby(pd.Grouper(key = 'value_time', freq = self.freq))['value'].mean())
                usgs_meanflow = usgs_meanflow.reset_index()

                #add key site information
                #make obs data the same as temporal means
                usgs_data = usgs_data.head(len(usgs_meanflow))

                #remove obs streamflow
                del usgs_data['value']
                del usgs_data['value_time']

                #connect mean temporal with other key info
                usgs_meanflow = pd.concat([usgs_meanflow, usgs_data], axis=1)
                usgs_meanflow = usgs_meanflow.rename(columns={'value_time':'Datetime', 'value':'USGS_flow','usgs_site_code':'USGS_ID', 'variable_name':'variable'})
                usgs_meanflow = usgs_meanflow.set_index('Datetime')
                usgs_meanflow.to_hdf(self.cwd+'/Data/NWIS/NWIS_sites_'+state+'.h5', key = site)
                
            except:
                siteA = '0'+str(site)
                service = IVDataService()
                usgs_data = service.get(
                    sites=siteA,
                    startDT= self.startDT,
                    endDT=self.endDT
                    )

                #Get Daily mean for Model comparision
                usgs_meanflow = pd.DataFrame(usgs_data.reset_index().groupby(pd.Grouper(key = 'value_time', freq = self.freq))['value'].mean())
                usgs_meanflow = usgs_meanflow.reset_index()

                #add key site information
                #make obs data the same as temporal means
                usgs_data = usgs_data.head(len(usgs_meanflow))

                #remove obs streamflow
                del usgs_data['value']
                del usgs_data['value_time']

                #connect mean temporal with other key info
                usgs_meanflow = pd.concat([usgs_meanflow, usgs_data], axis=1)
                usgs_meanflow = usgs_meanflow.rename(columns={'value_time':'Datetime', 'value':'USGS_flow','usgs_site_code':'USGS_ID', 'variable_name':'variable'})
                usgs_meanflow = usgs_meanflow.set_index('Datetime')
                usgs_meanflow.to_hdf(self.cwd+'/Data/NWIS/NWIS_sites_'+state+'.h5', key = site)
                
                
                
                
    def get_single_NWIS_site(self, site):
        # Retrieve data from a number of sites
        print('Retrieving USGS site: ', site, ' data')
       
        try:
            service = IVDataService()
            usgs_data = service.get(
                sites=str(site),
                startDT= self.startDT,
                endDT=self.endDT
                )

            #Get Daily mean for Model comparision
            usgs_meanflow = pd.DataFrame(usgs_data.reset_index().groupby(pd.Grouper(key = 'value_time', freq = self.freq))['value'].mean())
            usgs_meanflow = usgs_meanflow.reset_index()

            #add key site information
            #make obs data the same as temporal means
            usgs_data = usgs_data.head(len(usgs_meanflow))

            #remove obs streamflow
            del usgs_data['value']
            del usgs_data['value_time']

            #connect mean temporal with other key info
            usgs_meanflow = pd.concat([usgs_meanflow, usgs_data], axis=1)
            usgs_meanflow = usgs_meanflow.rename(columns={'value_time':'Datetime', 'value':'USGS_flow','usgs_site_code':'USGS_ID', 'variable_name':'variable'})
            usgs_meanflow = usgs_meanflow.set_index('Datetime')
            usgs_meanflow.to_hdf(self.cwd+'/Data/NWIS/NWIS_sites_'+self.state+'.h5', key = site)

        except:
            siteA = '0'+str(site)
            service = IVDataService()
            usgs_data = service.get(
                sites=siteA,
                startDT= self.startDT,
                endDT=self.endDT
                )

            #Get Daily mean for Model comparision
            usgs_meanflow = pd.DataFrame(usgs_data.reset_index().groupby(pd.Grouper(key = 'value_time', freq = self.freq))['value'].mean())
            usgs_meanflow = usgs_meanflow.reset_index()

            #add key site information
            #make obs data the same as temporal means
            usgs_data = usgs_data.head(len(usgs_meanflow))

            #remove obs streamflow
            del usgs_data['value']
            del usgs_data['value_time']

            #connect mean temporal with other key info
            usgs_meanflow = pd.concat([usgs_meanflow, usgs_data], axis=1)
            usgs_meanflow = usgs_meanflow.rename(columns={'value_time':'Datetime', 'value':'USGS_flow','usgs_site_code':'USGS_ID', 'variable_name':'variable'})
            usgs_meanflow = usgs_meanflow.set_index('Datetime')
            usgs_meanflow.to_hdf(self.cwd+'/Data/NWIS/NWIS_sites_'+self.state+'.h5', key = site)

            
            
    def Model_retrieve(self, df):
        
        # Retrieve data from a number of sites
        print('Retrieving model NHD reaches ', list(df.NHD_reachid), ' data')
        self.comparison_reaches = list(df.NHD_reachid)
        
        pbar = ProgressBar()
        for site in pbar(self.comparison_reaches):
            print('Getting data for: ', site)
            nwm_predictions = data.get_nwm_data(site,  self.startDT,  self.endDT)
            #I think NWM outputs are in cms...
            NHD_meanflow = nwm_predictions.resample(self.freq).mean()*self.cms_to_cfs
            NHD_meanflow = NHD_meanflow.reset_index()
            NHD_meanflow = NHD_meanflow.rename(columns={'time':'Datetime', 'value':'Obs_flow','feature_id':'NHD_segment', 'streamflow':'NHD_flow', 'velocity':'NHD_velocity'})
            NHD_meanflow = NHD_meanflow.set_index('Datetime')
            filepath = self.cwd+'/Data/'+self.model+'/NHD_segments_'+self.state+'.h5',
            NHD_meanflow.to_hdf(filepath, key = site)
           
            
            
            
    def get_single_NWM_reach(self, site):
        
        # Retrieve data from a number of sites
        print('Retrieving NHD Model reach: ', site, ' data')
        nwm_predictions = data.get_nwm_data(site,  self.startDT,  self.endDT)
        #I think NWM outputs are in cms...
        NHD_meanflow = nwm_predictions.resample(self.freq).mean()*self.cms_to_cfs
        NHD_meanflow = NHD_meanflow.reset_index()
        NHD_meanflow = NHD_meanflow.rename(columns={'time':'Datetime', 'value':'Obs_flow','feature_id':'NHD_segment', 'streamflow':'NHD_flow', 'velocity':'NHD_velocity'})
        NHD_meanflow = NHD_meanflow.set_index('Datetime')       
        filepath = self.cwd+'/Data/'+self.model+'/NHD_segments_'+self.state+'.h5',
        NHD_meanflow.to_hdf(filepath, key = site)
            
            
            
            
    def date_range_list(self, start_date, end_date):
        # Return list of datetime.date objects between start_date and end_date (inclusive).
        date_list = []
        curr_date = start_date
        while curr_date <= end_date:
            date_list.append(curr_date)
            curr_date += timedelta(days=1)
        return date_list      

    def prepare_comparison(self, df):
        
        self.comparison_reaches = list(df.NHD_reachid)
        self.NWIS_sites = list(df.NWIS_site_id)
        self.dates = self.date_range_list(pd.to_datetime(self.startDT), pd.to_datetime(self.endDT))
        
        self.NWIS_data = pd.DataFrame(columns = self.NWIS_sites)
        self.Mod_data = pd.DataFrame(columns = self.comparison_reaches)

        Mod_state_key =  dict(zip(df.NHD_reachid, 
                              df.state_id))
        
        print('Getting ', self.model, ' data')
        pbar = ProgressBar()
        for site in pbar(self.comparison_reaches):

            try:
                #print(f"Getting data for {self.model[:3]}: ", site)
                state = Mod_state_key[site].lower()
                format = '%Y-%m-%d %H:%M:%S'
                csv_key = f"{self.model}/NHD_segments_{state}.h5/{self.model[:3]}_{site}.csv"
                #print(csv_key)
                obj = self.bucket.Object(csv_key)
                body = obj.get()['Body']
                Mod_flow = pd.read_csv(body)
                Mod_flow.pop('Unnamed: 0')
                Mod_flow['time'] ='12:00:00' 
                Mod_flow['Datetime'] = pd.to_datetime(Mod_flow['Datetime']+ ' ' + Mod_flow['time'], format = format)
                Mod_flow.set_index('Datetime', inplace = True)
                Mod_flow = Mod_flow.loc[self.startDT:self.endDT]
                cols = Mod_flow.columns
                self.Mod_data[site] = Mod_flow[cols[0]]

            except:
                print('Site: ', site, ' not in database, skipping')
                #remove item from list
                self.comparison_reaches.remove(site)




        #Get NWIS data
        print('Getting NWIS data')
        pbar = ProgressBar()
        for site in pbar(self.NWIS_sites):
            try:
                
                #NWIS_meanflow =  pd.read_hdf(self.cwd+'/Data/NWIS/NWIS_sites_'+self.state+'.h5', key = str(site))
                csv_key = f"NWIS/NWIS_sites_{self.state}.h5/NWIS_{site}.csv"
                obj = self.bucket.Object(csv_key)
                body = obj.get()['Body']
                NWIS_meanflow = pd.read_csv(body)
                format = '%Y-%m-%d %H:%M:%S'
                NWIS_meanflow.drop_duplicates(subset = 'Datetime', inplace = True)                
                NWIS_meanflow['time'] ='12:00:00' 
                NWIS_meanflow['Datetime'] = pd.to_datetime(NWIS_meanflow['Datetime']+ ' ' + NWIS_meanflow['time'], format = format)
                NWIS_meanflow.set_index('Datetime', inplace = True)
                NWIS_meanflow = NWIS_meanflow.loc[self.startDT:self.endDT]
                
                #change np.nan to -100, can separate values out later
                self.NWIS_data[site] = -100


                #Adjust for different time intervals here
                #Daily
                #if self.freq =='D':
                self.NWIS_data[site] = NWIS_meanflow['USGS_flow']

            except:
                    print('USGS site ', site, ' not in database, skipping')
                    #remove item from list
                    self.NWIS_sites.remove(site)
        #change np.nan to -100, can separate values out later
        self.NWIS_data.fillna(-100, inplace = True)
        self.NWIS_column = self.NWIS_data.copy()
        self.NWIS_column = pd.DataFrame(self.NWIS_column.stack(), columns = ['NWIS_flow_cfs'])
        self.NWIS_column = self.NWIS_column.reset_index().drop('level_1', axis = 1)

        self.Mod_column = self.Mod_data.copy()
        col = self.model+'_flow_cfs'
        self.Mod_column = pd.DataFrame(self.Mod_column.stack(), columns = [col])
        self.Mod_column = self.Mod_column.reset_index().drop('level_1', axis =  1)

        
            
            
    def Model_Eval(self, df, size):

        #Creates a total categorical evaluation comparing model performacne
        print('Creating dataframe of all flow predictions to evaluate')
        self.Evaluation = pd.concat([self.Mod_column,self.NWIS_column], axis = 1)
        self.Evaluation = self.Evaluation.T.drop_duplicates().T     
        self.Evaluation = self.Evaluation.dropna()
        
        num_figs = len(self.comparison_reaches)


        fig, ax = plt.subplots(num_figs ,2, figsize = (10,4*num_figs))

        plot_title = 'Evaluation of ' + self.model + ' predictions related to watershed: ' + self.category + '-'+ size

        fig.suptitle(plot_title, y = 0.89)    
        
        self.Mod_data['datetime'] = self.dates
        self.Mod_data.set_index('datetime', inplace = True)
        
        self.NWIS_data['datetime'] = self.dates
        self.NWIS_data.set_index('datetime', inplace = True)

        for i in np.arange(0,num_figs,1):
            reach = self.comparison_reaches[i]
            site = self.NWIS_sites[i]

            NWIS_site_lab = 'USGS: ' + str(site)
            Mod_reach_lab = self.model + ': ' + str(reach)

            max_flow = max(max(self.NWIS_data[site]), max(self.Mod_data[reach]))
            min_flow = min(min(self.NWIS_data[site]), min(self.Mod_data[reach]))
            
            plt.subplots_adjust(hspace=0.5)

            ax[i,0].plot(self.Mod_data.index, self.Mod_data[reach], color = 'blue', label = Mod_reach_lab)
            ax[i,0].plot(self.NWIS_data.index, self.NWIS_data[site], color = 'orange',  label = NWIS_site_lab)
            ax[i,0].fill_between(self.NWIS_data.index, self.Mod_data[reach], self.NWIS_data[site], where= self.Mod_data[reach] >= self.NWIS_data[site], facecolor='orange', alpha=0.2, interpolate=True)
            ax[i,0].fill_between(self.NWIS_data.index, self.Mod_data[reach], self.NWIS_data[site], where= self.Mod_data[reach] < self.NWIS_data[site], facecolor='blue', alpha=0.2, interpolate=True)
            ax[i,0].set_xlabel('Datetime')
            ax[i,0].set_ylabel('Discharge (cfs)')
            ax[i,0].tick_params(axis='x', rotation = 45)
            ax[i,0].legend()
           

            ax[i,1].scatter(self.NWIS_data[site], self.Mod_data[reach], color = 'black')
            ax[i,1].plot([min_flow, max_flow],[min_flow, max_flow], ls = '--', c='red')
            ax[i,1].set_xlabel('Observed USGS (cfs)')
            ylab = self.model+ ' Predictions (cfs)'
            ax[i,1].set_ylabel(ylab)

        #calculate some performance metrics
        model_cfs = self.model+'_flow_cfs'
        r2 = r2_score(self.Evaluation.NWIS_flow_cfs, self.Evaluation.model_cfs)
        rmse = mean_squared_error(self.Evaluation.NWIS_flow_cfs, self.Evaluation.model_cfs, squared=False)
        maxerror = max_error(self.Evaluation.NWIS_flow_cfs, self.Evaluation.model_cfs)
        MAPE = mean_absolute_percentage_error(self.Evaluation.NWIS_flow_cfs, self.Evaluation.model_cfs)*100
        kge, r, alpha, beta = he.evaluator(he.kge, self.Evaluation.model_cfs.astype('float32'), self.Evaluation.NWIS_flow_cfs.astype('float32'))

        print('The '+ self.model+ ' demonstrates the following overall performance in catchments exhibiting ', size, ' ', self.category)
        print('RMSE = ', rmse, 'cfs')
        print('Maximum error = ', maxerror, 'cfs')
        print('Mean Absolute Percentage Error = ', MAPE, '%')
        print('Kling-Gupta Efficiency = ', kge[0])
        
        
        
        
        
    def Interactive_Model_Eval(self, freq, supply):
        self.freq = freq

        if self.freq == 'D':
            self.units = 'cfs'
        else:
            self.units = 'Acre-Feet'

        #Adjust for different time intervals here
        #Daily
        if self.freq == 'D':
            self.NWIS_data_resampled = self.NWIS_data.copy()
            self.Mod_data_resampled = self.Mod_data.copy()

        #Monthly, Quarterly, Annual
        if self.freq !='D':
            #NWIS
            self.NWIS_data_resampled = self.NWIS_data.copy()*self.cfsday_AFday
            self.NWIS_data_resampled = self.NWIS_data_resampled.resample(self.freq).sum()
            #Modeled
            self.Mod_data_resampled = self.Mod_data.copy()*self.cfsday_AFday
            self.Mod_data_resampled = self.Mod_data_resampled.resample(self.freq).sum()
            
        if supply == True:
            #NWIS
            #Get Columns names
            columns = self.NWIS_data_resampled.columns

            #set up cumulative monthly values
            self.NWIS_data_resampled['Year'] = self.NWIS_data_resampled.index.year

            self.NWIS_CumSum = pd.DataFrame(columns=columns)

            for site in columns:
                self.NWIS_CumSum[site] = self.NWIS_data_resampled.groupby(['Year'])[site].cumsum()

            #Model
            #Get Columns names
            columns = self.Mod_data_resampled.columns

            #set up cumulative monthly values
            self.Mod_data_resampled['Year'] = self.Mod_data_resampled.index.year

            self.Mod_CumSum = pd.DataFrame(columns=columns)

            for site in columns:
                self.Mod_CumSum[site] = self.Mod_data_resampled.groupby(['Year'])[site].cumsum()
                
            #set the Mod and NWIS resampled data == to the CumSum Df's
            self.NWIS_data_resampled = self.NWIS_CumSum
            self.Mod_data_resampled =self.Mod_CumSum

        RMSE = []
        MAXERROR = []
        MAPE = []
        KGE = []

        for row in np.arange(0,len(self.df),1):
            #try:            
            #print(row)
            #Get NWIS id
            NWISid = self.df['NWIS_site_id'][row]
            
            #Get Model reach id
            reachid = 'NHD_reachid'
            modid = self.df[reachid][row]
            
            #get observed and prediction data
            obs = self.NWIS_data_resampled[NWISid]
            mod = self.Mod_data_resampled[modid]

            #remove na values or 0
            df = pd.DataFrame()
            df['obs'] = obs
            df['mod'] = mod.astype('float64')
         #adding dropna() to prevent crashing script
            df = df[df>=0]
            df.dropna(inplace =True)

            if len(df)>=1:
                
                df[df<0.01]=0.01
                df['error'] = df['obs'] - df['mod']
                df['P_error'] = abs(df['error']/df['obs'])*100
                
                #drop inf values
                df.replace([np.inf, -np.inf], np.nan, inplace = True)
                df.dropna(inplace = True)

                obs = df['obs']
                mod = df['mod']

                #calculate scoring
                rmse = round(mean_squared_error(obs, mod, squared=False))
                maxerror = round(max_error(obs, mod))
                mape = df.P_error.mean()
                kge, r, alpha, beta = he.evaluator(he.kge, mod.astype('float32'), obs.astype('float32'))

                RMSE.append(rmse)
                MAXERROR.append(maxerror)
                MAPE.append(mape)
                KGE.append(kge[0])

              
            else:
                #calculate scoring
                rmse = 0
                maxerror = 0
                mape = 0
                kge = -10000

                RMSE.append(rmse)
                MAXERROR.append(maxerror)
                MAPE.append(mape)
                KGE.append(kge)
      

        #Connect model evaluation to a DF, add in relevant information concerning LULC
        Eval = pd.DataFrame()
        Eval['NWIS_site_id'] = self.df['NWIS_site_id']
        Eval[reachid] = self.df[reachid]
        Eval['Location'] = self.df['NWIS_sitename']
        Eval['RMSE'] = RMSE
        Eval['MaxError'] = MAXERROR
        Eval['MAPE'] = MAPE
        Eval['KGE'] = KGE
        Eval['Drainage_area_mi2'] = self.df['Drainage_area_mi2']
        Eval['Mean_Basin_Elev_ft'] = self.df['Mean_Basin_Elev_ft']
        Eval['Perc_Forest'] = self.df['Perc_Forest']
        Eval['Perc_Imperv'] = self.df['Perc_Imperv']
        Eval['Perc_Herbace'] = self.df['Perc_Herbace']
        Eval['Mean_Ann_Precip_in'] = self.df['Mean_Ann_Precip_in']
        Eval['Ann_low_cfs'] = self.df['Ann_low_cfs']
        Eval['Ann_mean_cfs'] = self.df['Ann_mean_cfs']
        Eval['Ann_hi_cfs'] = self.df['Ann_hi_cfs']
        Eval['Location'] = self.df['NWIS_sitename']
        Eval[self.category] = self.df[self.category]
        #remove locations with no USGS obs
        Eval = Eval[Eval['KGE'] > -1000]
        #sort dataframe and reindex
        self.Eval = Eval.sort_values('KGE', ascending = False).reset_index(drop = True)    
        #display evaluation DF
        display(self.Eval)
        
        #plot the model performance vs LULC to identify any relationships indicating where/why model performance
        #make all very negative KGE values -1
        self.Eval['KGE'][self.Eval['KGE'] < -1] = -1

        fig, ax = plt.subplots(3, 3, figsize = (11,11))
        fig.suptitle('Watershed Charcteristics vs. Model Performance', fontsize = 16)

        ax1 = ['Drainage_area_mi2', 'Mean_Basin_Elev_ft', 'Perc_Forest']
        for var in np.arange(0,len(ax1),1):
            variable = ax1[var]
             #remove na values for variables to make trendline
            cols = ['KGE', variable]
            df = self.Eval[cols]
            df.dropna(axis = 0, inplace = True)
            x = df['KGE']
            y = df[variable]
            ax[0,var].scatter(x = x, y = y)
            ax[0,var].set_ylabel(ax1[var])
            #calculate equation for trendline
            try:
                z = np.polyfit(x, y, 1)
                p = np.poly1d(z)
                ax[0,var].plot(x, p(x), color = 'r', linestyle = '--')
            except:
                pass

        ax2 = ['Perc_Imperv', 'Perc_Herbace', 'Mean_Ann_Precip_in']
        for var in np.arange(0,len(ax2),1):
            variable = ax2[var]
             #remove na values for variables to make trendline
            cols = ['KGE', variable]
            df = self.Eval[cols]
            df.dropna(axis = 0, inplace = True)
            x = df['KGE']
            y = df[variable]
            ax[1,var].scatter(x = x, y = y)
            ax[1,var].set_ylabel(ax2[var])
            
            #calculate equation for trendline
            try:
                z = np.polyfit(x, y, 1)
                p = np.poly1d(z)
                #add trendline to plot
                ax[1,var].plot(x, p(x), color = 'r', linestyle = '--')
            except:
                pass

        ax3 = ['Ann_low_cfs', 'Ann_mean_cfs', 'Ann_hi_cfs']
        for var in np.arange(0,len(ax3),1):
            variable = ax3[var]
            #remove na values for variables to make trendline
            cols = ['KGE', variable]
            df = self.Eval[cols]
            df.dropna(axis = 0, inplace = True)
            x = df['KGE']
            y = df[variable]
            ax[2,var].scatter(x = x, y = y)
            ax[2,var].set_xlabel('Model Performance (KGE)')
            ax[2,var].set_ylabel(ax3[var])
             #add trendline
            #calculate equation for trendline
            try:
                z = np.polyfit(x, y, 1)
                p = np.poly1d(z)
                #add trendline to plot
                ax[2,var].plot(x, p(x), color = 'r', linestyle = '--')
            except:
                pass


        plt.tight_layout()
        plt.show()
        
        num_figs = len(self.Eval)
        for i in np.arange(0,num_figs,1):
            

            reach = self.Eval[reachid][i]
            site = self.Eval['NWIS_site_id'][i]
            #print(site, reach)
 
            sitename = self.Eval.Location[i]
            sitestat = str(self.Eval[self.category][i])

            plot_title = 'Performance of ' + self.model +' predictions related to: ' + self.category +  '\n' + sitename + '\n'+ self.category +': ' + sitestat + ', classified as: '+ self.size


            NWIS_site_lab = 'USGS: ' + str(site)
            Mod_reach_lab = self.model + ': NHD ' + str(reach)

            Eval_cols = [NWIS_site_lab, Mod_reach_lab]
            Eval_df = pd.DataFrame(index = self.NWIS_data_resampled.index, columns = Eval_cols)
            Eval_df[Mod_reach_lab] = self.Mod_data_resampled[reach]
            Eval_df[NWIS_site_lab] = self.NWIS_data_resampled[site]


            Eval_df = Eval_df.dropna()

            if Eval_df.shape[0] > 0:

                #need to have datetime fixed
                Eval_df = Eval_df.reset_index()
                Eval_df['Datetime'] = pd.to_datetime(Eval_df['Datetime'])
                Eval_df.set_index('Datetime', inplace = True, drop = True)
                
                #get observed and prediction data
                obs = Eval_df[NWIS_site_lab]
                mod = Eval_df[Mod_reach_lab]
                df = pd.DataFrame()
                df['obs'] = obs
                df['mod'] = mod.astype('float64')
                #remove na values or 0
                df[df<0.01]=0.01
                #adding dropna() to prevent crashing script
                df.dropna(axis=0, inplace =True)
                
                if len(df)>0:
                    df['error'] = df['obs'] - df['mod']
                    df['P_error'] = abs(df['error']/df['obs'])*100
                    #drop inf values
                    df.replace([np.inf, -np.inf], np.nan, inplace = True)
                    df.dropna(inplace = True)

                    obs = df['obs']
                    mod = df['mod']

                    #calculate scoring
                    rmse = round(mean_squared_error(obs, mod, squared=False))
                    maxerror = round(max_error(obs, mod))
                    MAPE = round(mean_absolute_percentage_error(obs, mod)*100)
                    kge, r, alpha, beta = he.evaluator(he.kge, mod.astype('float32'), obs.astype('float32'))

                    #set limit to MAPE error
                    if MAPE > 1000:
                        MAPE ='> 1000'

                    rmse_phrase = 'RMSE: ' + str(rmse) + ' ' + self.units
                    error_phrase = 'Max Error: ' + str(maxerror) + ' ' + self.units
                    mape_phrase = 'MAPE: ' + str(MAPE) + '%'
                    kge_phrase = 'kge: ' + str(round(kge[0],2))

                    max_flow = max(max(Eval_df[NWIS_site_lab]), max(Eval_df[Mod_reach_lab]))
                    min_flow = min(min(Eval_df[NWIS_site_lab]), min(Eval_df[Mod_reach_lab]))

                    flow_range = np.arange(min_flow, max_flow, (max_flow-min_flow)/100)

                    if self.freq == 'A':
                        bbox_L = -int(round((len(Eval_df)*.32),0))
                        text_bbox_L = -int(round((len(Eval_df)*.22),0))

                    else:
                        bbox_L = -int(round((len(Eval_df)*.32),0))
                        text_bbox_L = -int(round((len(Eval_df)*.16),0))

                    Discharge_lab = 'Discharge (' +self.units +')'
                    Obs_Discharge_lab = ' Observed Discharge (' +self.units +')'
                    Mod_Discharge_lab = self.model +' Discharge (' +self.units +')'


                    NWIS_hydrograph = hv.Curve((Eval_df.index, Eval_df[NWIS_site_lab]), 'DateTime', Discharge_lab, label = NWIS_site_lab).opts(title = plot_title, tools = ['hover'], color = 'orange')
                    Mod_hydrograph = hv.Curve((Eval_df.index, Eval_df[Mod_reach_lab]), 'DateTime', Discharge_lab, label = Mod_reach_lab).opts(tools = ['hover'], color = 'blue')
                    RMSE_hv = hv.Text(Eval_df.index[text_bbox_L],max_flow*.93, rmse_phrase, fontsize = 8)
                    Error_hv = hv.Text(Eval_df.index[text_bbox_L],max_flow*.83, error_phrase, fontsize = 8)
                    MAPE_hv = hv.Text(Eval_df.index[text_bbox_L],max_flow*.73, mape_phrase, fontsize = 8)
                    KGE_hv = hv.Text(Eval_df.index[text_bbox_L],max_flow*.63, kge_phrase, fontsize = 8)
                    textbox_hv = hv.Rectangles([(Eval_df.index[bbox_L], max_flow*.56, Eval_df.index[-1], max_flow*.99)]).opts(color = 'white')

                    Mod_NWIS_Scatter = hv.Scatter((Eval_df[NWIS_site_lab], Eval_df[Mod_reach_lab]), Obs_Discharge_lab, Mod_Discharge_lab).opts(tools = ['hover'], color = 'blue', xrotation=45)
                    Mod_NWIS_one2one = hv.Curve((flow_range, flow_range)).opts(color = 'red', line_dash='dashed')
                    display((NWIS_hydrograph * Mod_hydrograph*textbox_hv*RMSE_hv*Error_hv*MAPE_hv*KGE_hv).opts(width=600, legend_position='top_left', tools=['hover']) + (Mod_NWIS_Scatter*Mod_NWIS_one2one).opts(shared_axes = False))

                else:
                    print('No data for NWIS site: ', str(NWIS_site_lab), ' skipping.')

            
    #streamstats does not get lat long, we need this to do any NWIS geospatial work
    #https://github.com/hyriver/HyRiver-examples/blob/main/notebooks/dam_impact.ipynb
    def more_StreamStats(self, state, cwd):
        start = "2019-01-01"
        end = "2020-01-01"
        nwis = NWIS()
        query = {
            "stateCd": state,
            "startDt": start,
            "endDt": end,
            "outputDataTypeCd": "dv",  # daily values
            "hasDataTypeCd": "dv",  # daily values
            "parameterCd": "00060",  # discharge
        }
        sites = nwis.get_info(query)
        sites = sites.drop_duplicates(subset = ['site_no'])
        sites['site_no'] = sites['site_no'].astype(str).astype('int64')
        sites = sites[sites['site_no'] < 20000000].reset_index(drop =  True)
        sites['site_no'] = sites['site_no'].astype(str)

        for site in np.arange(0, len(sites),1):
            if len(sites['site_no'][site]) == 7:
                sites['site_no'][site] = '0'+sites['site_no'][site]


        cols = ['site_no', 'station_nm', 'dec_lat_va',
               'dec_long_va', 'alt_va',
               'alt_acy_va', 'huc_cd', 'parm_cd',
               'begin_date', 'end_date',
               'drain_sqkm',  'geometry']
        sites = sites[cols]    

        sites.to_csv(cwd+ '/Data/StreamStats/more_stats/'+ state+'.csv')
        
        
        
        #Map locations and scoring of sites
    def Map_Plot_Eval(self, freq, df, size, supply):
        self.freq = freq
        self.df = df
        self.size = size

        if self.freq == 'D':
            self.units = 'cfs'
        else:
            self.units = 'Acre-Feet'

        yaxis = 'Streamflow (' + self.units +')'
        
        #Get data and prepare
        self.prepare_comparison(self.df)

        #Adjust for different time intervals here
        #Daily
        if self.freq == 'D':
            self.NWIS_data_resampled = self.NWIS_data.copy()
            self.Mod_data_resampled = self.Mod_data.copy()


        #Monthly, Quarterly, Annual
        if self.freq !='D':
            #NWIS
            self.NWIS_data_resampled = self.NWIS_data.copy()*self.cfsday_AFday
            self.NWIS_data_resampled = self.NWIS_data_resampled.resample(self.freq).sum()
            #Modeled
            self.Mod_data_resampled = self.Mod_data.copy()*self.cfsday_AFday
            self.Mod_data_resampled = self.Mod_data_resampled.resample(self.freq).sum()
            
        if supply == True:
            #NWIS
            #Get Columns names
            columns = self.NWIS_data_resampled.columns

            #set up cumulative monthly values
            self.NWIS_data_resampled['Year'] = self.NWIS_data_resampled.index.year

            self.NWIS_CumSum = pd.DataFrame(columns=columns)

            for site in columns:
                self.NWIS_CumSum[site] = self.NWIS_data_resampled.groupby(['Year'])[site].cumsum()

            #Model
            #Get Columns names
            columns = self.Mod_data_resampled.columns

            #set up cumulative monthly values
            self.Mod_data_resampled['Year'] = self.Mod_data_resampled.index.year

            self.Mod_CumSum = pd.DataFrame(columns=columns)

            for site in columns:
                self.Mod_CumSum[site] = self.Mod_data_resampled.groupby(['Year'])[site].cumsum()
                
            #set the Mod and NWIS resampled data == to the CumSum Df's
            self.NWIS_data_resampled = self.NWIS_CumSum
            self.Mod_data_resampled =self.Mod_CumSum

        print('Plotting monitoring station locations')
        cols =  ['NWIS_site_id', 'NWIS_sitename', 'NHD_reachid', 'dec_lat_va', 'dec_long_va', 'geometry']

        self.df_map = self.df[cols]
        self.df_map.reset_index(inplace = True, drop = True) 
        #Get Centroid of watershed
        centeroid = self.df_map.dissolve().centroid

        # Create a Map instance
        m = folium.Map(location=[centeroid.y[0], 
                                 centeroid.x[0]], 
                                 #tiles = 'Open street map ', 
                                tiles='http://services.arcgisonline.com/arcgis/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}',
                                 attr="Sources: National Geographic, Esri, Garmin, HERE, UNEP-WCMC, USGS, NASA, ESA, METI, NRCAN, GEBCO, NOAA, INCREMENT P",
                                 zoom_start=8, 
                       control_scale=True)
        #add legend to map
        colormap = cm.StepColormap(colors = ['red', 'orange', 'lightgreen', 'g'], vmin = -1, vmax = 1, index = [-1,-0.4,0,0.3,1])
        colormap.caption = 'Model Performance (KGE)'
        m.add_child(colormap)

        ax = AxisProperties(
        labels=PropertySet(
            angle=ValueRef(value=300),
            align=ValueRef(value='right')
                )
            )

        for i in np.arange(0, len(self.df_map),1):
            #get site information
            site = self.df_map['NWIS_site_id'][i]
            USGSsite = 'USGS station id: ' + site
            site_name = self.df_map['NWIS_sitename'][i]

            reach = self.df_map['NHD_reachid'][i]
            Modreach = self.model +' reach id: ' + str(reach)
            
            #get modeled and observed information for each site
            df = pd.DataFrame(self.NWIS_data_resampled[site])
            df = df.rename(columns = {site: USGSsite})
            df[Modreach] = pd.DataFrame(self.Mod_data_resampled[reach])
            
            #this evaluates the model only on NWIS observations
            df_narem = pd.DataFrame()
            df_narem['obs'] = self.NWIS_data_resampled[site].astype('float64')
            df_narem['mod'] = self.Mod_data_resampled[reach].astype('float64')
            
            #remove locations with no usgs observations for time period -remove na values or 0, 
            df_narem = df_narem[df_narem>=0]
            df_narem.dropna(inplace = True)
            
            if len(df_narem)>=1:
            
               # df_narem['error'] = df_narem['obs'] - df_narem['mod']
               # df_narem['P_error'] = abs(df_narem['error']/df_narem['obs'])*100
                #drop inf values
                df_narem.replace([np.inf, -np.inf], np.nan, inplace = True)
                df_narem.dropna(inplace = True)

                obs = df_narem['obs']
                mod = df_narem['mod']

                #calculate scoring
                kge, r, alpha, beta = he.evaluator(he.kge, mod.astype('float32'), obs.astype('float32'))

                 #set the color of marker by model performance
                #Marker color options ['red', 'blue', 'green', 'purple', 'orange', 'darkred', 'lightred', 'beige', 'darkblue', 'darkgreen', 'cadetblue', 'darkpurple', 'white', 'pink', 'lightblue', 'lightgreen', 'gray', 'black', 'lightgray']

                if kge[0] > 0.30:
                    color = 'green'

                elif kge[0] > 0.0:
                    color = 'lightgreen'

                elif kge[0] > -0.40:
                    color = 'orange'

                else:
                    color = 'red'


                title_size = 14
                
                self.dff = df
                #create graph and convert to json
                graph = vincent.Line(df, height=300, width=500)
                graph.axis_titles(x='Datetime', y=yaxis)
                graph.legend(title= site_name)
                graph.colors(brew='Set1')
                graph.x_axis_properties(title_size=title_size, title_offset=35,
                              label_angle=300, label_align='right', color=None)
                graph.y_axis_properties(title_size=title_size, title_offset=-30,
                              label_angle=None, label_align='right', color=None)

                data = json.loads(graph.to_json())

                #Add marker with point to map, https://fontawesome.com/v4/cheatsheet/
                lat = self.df_map['dec_lat_va'][i]
                long = self.df_map['dec_long_va'][i]
                mk = features.Marker([lat, long], icon=folium.Icon(color=color, icon = 'fa-navicon', prefix = 'fa'))
                p = folium.Popup("Hello")
                v = features.Vega(data, width="100%", height="100%")

                mk.add_child(p)
                p.add_child(v)
                m.add_child(mk)


        display(m)
