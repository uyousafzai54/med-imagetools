import os
import warnings
import datetime

import numpy as np
from matplotlib import pyplot as plt

import SimpleITK as sitk
from pydicom import dcmread

def read_image(path):
    reader = sitk.ImageSeriesReader()
    dicom_names = reader.GetGDCMSeriesFileNames(path)
    reader.SetFileNames(dicom_names)
    reader.MetaDataDictionaryArrayUpdateOn()
    reader.LoadPrivateTagsOn()

    return reader.Execute()

class PET(sitk.Image):
    def __init__(self, img_pet, df):
        super().__init__(img_pet)
        self.img_pet = img_pet
        self.df = df
    
    @classmethod
    def from_dicom_pet(cls, path, type="SUV"):
        '''
        Reads the PET scan and returns the data frame and the image dosage in SITK format
        There are two types of existing formats which has to be mentioned in the type
        type:
            SUV: gets the image with each pixel value having SUV value
            ACT: gets the image with each pixel value having activity concentration
        SUV = Activity concenteration/(Injected dose quantity/Body weight)

        Please refer to the pseudocode: https://qibawiki.rsna.org/index.php/Standardized_Uptake_Value_(SUV) 
        If there is no data on SUV/ACT then backup calculation is done based on the formula in the documentation, although, it may
        have some error.
        '''
        pet      = read_image(path)
        path_one = os.path.join(path,os.listdir(path)[0])
        df       = dcmread(path_one)
        try:
            if type=="SUV":
                factor = df.to_json_dict()['70531000']["Value"][0]
            else:
                factor = df.to_json_dict()['70531009']['Value'][0]
        except:
            warnings.warn("Scale factor not available in DICOMs. Calculating based on metadata, may contain errors")
            factor = cls.calc_factor(df,type)
        img_pet = sitk.Cast(pet, sitk.sitkFloat32)

        #SimpleITK reads some pixel values as negative but with correct value
        img_pet = sitk.Abs(img_pet * factor)
        return cls(img_pet, df)
        
    def get_metadata(self):
        '''
        Forms the important metadata for reference in the dictionary format
        {
            scan_time (in seconds): AcquisitionTime 
            injection_time (in seconds): RadiopharmaceuticalInformationSequence[0].RadiopharmaceuticalStartTime
            weight (in kg): PatientWeight
            half_life (in seconds): RadiopharmaceuticalInformationSequence[0].RadionuclideHalfLife
            injected_dose: RadiopharmaceuticalInformationSequence[0].RadionuclideTotalDose
        }
        '''
        self.metadata = {}
        self.metadata["weight"] = float(self.df.PatientWeight)
        self.metadata["scan_time"] = float(self.df.AcquisitionTime) / 1000
        self.metadata["injection_time"] = float(self.df.RadiopharmaceuticalInformationSequence[0].RadiopharmaceuticalStartTime) / 1000
        self.metadata["half_life"] = float(self.df.RadiopharmaceuticalInformationSequence[0].RadionuclideHalfLife)
        self.metadata["injected_dose"] = float(self.df.RadiopharmaceuticalInformationSequence[0].RadionuclideTotalDose)
        
        return self.metadata

    def resample_pet(self,
                     ct_scan: sitk.Image) -> sitk.Image:
        '''
        Resamples the PET scan so that it can be overlayed with CT scan. The beginning and end slices of the 
        resampled PET scan might be empty due to the interpolation
        '''
        resampled_pt = sitk.Resample(self.img_pet, ct_scan, interpolator=sitk.sitkNearestNeighbor)
        return resampled_pt

    def show_overlay(self,
                     ct_scan: sitk.Image,
                     slice_number: int) -> plt.figure:
        '''
        For a given slice number, the function resamples PET scan and overlays on top of the CT scan and returns the figure of the
        overlay
        '''
        resampled_pt = self.resample_pet(ct_scan)
        fig = plt.figure("Overlayed image", figsize=[15, 10])
        pt_arr = sitk.GetArrayFromImage(resampled_pt)
        plt.subplot(1,3,1)
        plt.imshow(pt_arr[slice_number,:,:])
        plt.subplot(1,3,2)
        ct_arr = sitk.GetArrayFromImage(ct_scan)
        plt.imshow(ct_arr[slice_number,:,:])
        plt.subplot(1,3,3)
        plt.imshow(ct_arr[slice_number,:,:], cmap=plt.cm.gray)
        plt.imshow(pt_arr[slice_number,:,:], cmap=plt.cm.hot, alpha=.4)
        return fig

    @staticmethod
    def calc_factor(df, type: str):
        '''
        Following the calculation formula stated in https://gist.github.com/pangyuteng/c6a075ba9aa00bb750468c30f13fc603
        '''
        #Fetching some required Meta Data
        try:
            weight = float(df.PatientWeight) * 1000
        except:
            warnings.warn("Patient Weight Not Present. Taking 75Kg")
            weight = 75000
        try:
            scan_time = datetime.datetime.strptime(df.AcquisitionTime, '%H%M%S.%f')
            injection_time = datetime.datetime.strptime(df.RadiopharmaceuticalInformationSequence[0].RadiopharmaceuticalStartTime, '%H%M%S.%f')
            half_life = float(df.RadiopharmaceuticalInformationSequence[0].RadionuclideHalfLife)
            injected_dose = float(df.RadiopharmaceuticalInformationSequence[0].RadionuclideTotalDose)

            # Calculate activity concenteration factor
            a = np.exp(-np.log(2) * ((scan_time - injection_time).seconds / half_life))

            # Calculate SUV factor
            injected_dose_decay = a * injected_dose
        except:
            warnings.warn("Not enough data available, taking average values")
            a = np.exp(-np.log(2) * (1.75 * 3600) / 6588) # 90 min waiting time, 15 min preparation
            injected_dose_decay = 420000000 * a # 420 MBq

        suv = weight/injected_dose_decay
        if type == "SUV":
            return suv
        else:
            return 1/a
